"""Repeated PFV polling reads covered work without joining SQLite's writer queue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session
from test_steward_snapshot_fences import pending_view, world

from app import config
from app.models.account import Account
from app.models.space import SpaceMember
from app.models.steward import (
    StewardGenerationView,
    StewardJob,
    StewardViewDemand,
    StewardViewTarget,
)
from app.models.v2_foundation import DomainEvent
from app.services import (
    steward,
    steward_demand,
    steward_pipeline,
    steward_runtime,
    steward_snapshot,
    steward_views,
)
from app.services.relationship_resolver import advance_search, resolve_graph
from app.utils.timeutil import utcnow


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", False)
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)
    monkeypatch.setattr(steward_runtime, "run_slice", advance_search)


@pytest.fixture
def covered_view(db_session):
    fixture = world(db_session)
    generation, work, snapshot = pending_view(db_session, fixture)
    demand = StewardViewDemand(
        space_id=fixture.space.id,
        viewer_account_id=fixture.viewer.account.id,
        revision=1,
        fulfilled_revision=0,
        requested_at=utcnow() - timedelta(minutes=1),
    )
    db_session.add(demand)
    view = db_session.get(StewardGenerationView, work.view_id)
    assert view is not None
    view.demand_revision = demand.revision
    db_session.commit()
    return fixture, generation, work, snapshot, demand


@contextmanager
def _statements(bind):
    statements = []

    def observe(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", observe)


def _assert_read_only(statements):
    assert "BEGIN" in statements  # Authorization and coverage share a real SQLite snapshot.
    assert not any("BEGIN IMMEDIATE" in statement.upper() for statement in statements)
    assert not any(
        statement.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}
        for statement in statements
    )
    assert not any(
        "skeleton_json" in statement or "checkpoint_json" in statement for statement in statements
    )


def _register_while_writer_held(session, fixture):
    bind = session.get_bind()
    account, space_id = fixture.viewer.account, fixture.space.id
    with ThreadPoolExecutor(max_workers=1) as worker:
        with Session(bind=bind) as writer:
            assert writer.connection().exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            writer.connection().exec_driver_sql("BEGIN IMMEDIATE")
            writer.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(failed_attempts=Account.failed_attempts + 1)
            )
            with _statements(bind) as statements:
                future = worker.submit(steward_demand.register, account=account, space_id=space_id)
                try:
                    result = future.result(timeout=2)
                finally:
                    # Release even on regression, so executor cleanup cannot deadlock.
                    writer.rollback()
    return result, statements


def test_running_demand_coalesces_while_another_connection_holds_writer(db_session, covered_view):
    fixture, _generation, _work, _snapshot, demand = covered_view
    original = (demand.revision, demand.fulfilled_revision, demand.requested_at)
    result, statements = _register_while_writer_held(db_session, fixture)
    assert result == "already_active"
    _assert_read_only(statements)
    db_session.refresh(demand)
    assert (demand.revision, demand.fulfilled_revision, demand.requested_at) == original


def test_published_view_coalesces_after_its_completion_event(db_session):
    fixture = world(db_session)
    upper = fixture.job.trigger_cursor
    summary = steward_pipeline.execute(db_session, fixture.job, now=utcnow(), upper=upper)
    steward_pipeline.publish(db_session, fixture.binding, summary=summary, upper=upper)
    assert steward.current_event_watermark(db_session) > upper
    result, statements = _register_while_writer_held(db_session, fixture)
    assert result == "already_active"
    _assert_read_only(statements)
    assert db_session.scalar(select(StewardViewDemand.id)) is None


@pytest.mark.parametrize(
    "change",
    [
        "missing_demand",
        "new_revision",
        "fulfilled",
        "missing_view",
        "view_failed",
        "root_changed",
        "input_changed",
        "expired_generation",
        "expired_lease",
        "new_attempt",
        "new_owner",
        "queued",
        "focus",
        "retry",
        "higher_watermark",
    ],
)
def test_uncovered_or_explicit_demand_preserves_writer_and_wakeup(
    db_session, covered_view, monkeypatch, change
):
    fixture, generation, work, _snapshot, demand = covered_view
    view = db_session.get(StewardGenerationView, work.view_id)
    assert view is not None
    if change == "missing_demand":
        db_session.delete(demand)
    elif change == "new_revision":
        demand.revision += 1
    elif change == "fulfilled":
        demand.fulfilled_revision = demand.revision
    elif change == "missing_view":
        db_session.delete(view)
    elif change == "view_failed":
        view.status = "failed"
    elif change == "root_changed":
        view.root_user_id = fixture.target.id
    elif change == "input_changed":
        fixture.target.gender = "m"
    elif change == "expired_generation":
        generation.valid_until = utcnow() - timedelta(seconds=1)
    elif change == "expired_lease":
        fixture.job.lease_expires_at = utcnow() - timedelta(seconds=1)
    elif change == "new_attempt":
        fixture.job.attempt += 1
    elif change == "new_owner":
        fixture.job.leased_by = "replacement-owner"
    elif change == "queued":
        fixture.job.status = "queued"
    elif change == "higher_watermark":
        db_session.add(
            DomainEvent(
                type="memory.test",
                aggregate_type="memory",
                aggregate_id=1,
                payload={},
                created_at=utcnow(),
            )
        )
    db_session.commit()
    watermark = steward.current_event_watermark(db_session)
    if change == "higher_watermark":
        assert watermark > fixture.job.trigger_cursor
    wakeups = []
    monkeypatch.setattr(steward_runtime, "launch_due", lambda **kwargs: wakeups.append(kwargs))
    requested_at = utcnow()
    with _statements(db_session.get_bind()) as statements:
        assert (
            steward_demand.register(
                account=fixture.viewer.account,
                space_id=fixture.space.id,
                focus_user_id=fixture.target.id if change == "focus" else None,
                retry=change == "retry",
            )
            == "already_active"
        )
    assert any("BEGIN IMMEDIATE" in statement.upper() for statement in statements)
    assert wakeups == [{"space_id": fixture.space.id, "limit": 1}]
    db_session.expire_all()
    saved = db_session.scalar(select(StewardViewDemand))
    assert saved is not None and saved.requested_at >= requested_at
    assert saved.revision == (2 if change in {"new_revision", "fulfilled", "retry"} else 1)
    assert saved.fulfilled_revision == (1 if change == "fulfilled" else 0)
    if change == "focus":
        assert saved.focus_user_id == fixture.target.id
    job = db_session.get(StewardJob, fixture.job.id)
    assert job is not None and job.trigger_cursor >= watermark


@pytest.mark.parametrize("change, status", [("membership", 404), ("token", 401)])
def test_coalescing_rechecks_authorization(db_session, covered_view, change, status):
    fixture, _generation, _work, _snapshot, demand = covered_view
    account = fixture.viewer.account
    with Session(bind=db_session.get_bind()) as writer:
        if change == "membership":
            writer.execute(
                update(SpaceMember)
                .where(
                    SpaceMember.space_id == fixture.space.id,
                    SpaceMember.user_id == fixture.viewer.id,
                )
                .values(status="removed")
            )
        else:
            writer.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(token_version=Account.token_version + 1)
            )
        writer.commit()
    with _statements(db_session.get_bind()) as statements:
        with pytest.raises(HTTPException) as exc:
            steward_demand.register(account=account, space_id=fixture.space.id)
    assert exc.value.status_code == status
    _assert_read_only(statements)
    db_session.refresh(demand)
    assert demand.revision == 1 and demand.fulfilled_revision == 0


@pytest.mark.parametrize("tampered", [False, True])
def test_poll_reads_only_display_columns_and_still_checks_evidence(
    db_session, covered_view, tampered
):
    fixture, generation, work, snapshot, _demand = covered_view
    resolution = resolve_graph(snapshot.graph, target_user_id=fixture.target.id)
    steward_pipeline.save_target(
        db_session.get_bind(),
        fixture.binding,
        generation.id,
        work.view_id,
        snapshot=snapshot,
        resolution=resolution,
    )
    bind = db_session.get_bind()
    with Session(bind=bind) as writer:
        if tampered:
            edge = writer.scalar(
                select(StewardViewTarget.edge_json).where(StewardViewTarget.view_id == work.view_id)
            )
            assert edge is not None
            edge["path"][0]["fact_id"] = -1
            writer.execute(
                update(StewardViewTarget)
                .where(StewardViewTarget.view_id == work.view_id)
                .values(edge_json=edge)
            )
        # The internal search cache is intentionally undecodable. A display
        # request must never load it, even through an ORM JSON result processor.
        writer.execute(
            text("UPDATE steward_view_targets SET resolution_json = :raw WHERE view_id = :view"),
            {"raw": "not-json", "view": work.view_id},
        )
        writer.commit()
    with _statements(bind) as statements:
        with steward_snapshot.read_transaction(bind) as reader:
            payload, _valid_until = steward_views.payload_for(
                reader, account=fixture.viewer.account, space_id=fixture.space.id, progressive=True
            )
    assert payload is not None
    assert {node["user_id"] for node in payload["nodes"]} == {fixture.viewer.id, fixture.target.id}
    if tampered:
        assert payload["edges"] == []
        assert payload["progress"]["targets"] == [
            {"user_id": fixture.target.id, "status": "failed", "reason_code": "evidence_invalid"}
        ]
    else:
        assert [edge["to_user_id"] for edge in payload["edges"]] == [fixture.target.id]
        assert payload["progress"]["phase"] == "ready"
    target_queries = [statement for statement in statements if "steward_view_targets" in statement]
    assert target_queries and all(
        "resolution_json" not in statement for statement in target_queries
    )
