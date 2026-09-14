"""Independent-connection checks for snapshot, lease and publication boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from conftest import create_agent_fixture, create_space_member, create_user_with_pin
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import config
from app.models.notification import Notification
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.models.steward import (
    ActionCard,
    StewardDeliveryIntent,
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardViewDemand,
    StewardViewTarget,
)
from app.models.user import User
from app.models.v2_foundation import DomainEvent
from app.services import (
    domain_events,
    steward,
    steward_delivery,
    steward_pipeline,
    steward_runtime,
    steward_snapshot,
)
from app.services.relationship_resolver import advance_search, resolve_graph
from app.services.source_facts import create_source_fact, transition_source_fact
from app.utils.timeutil import utcnow


@dataclass(frozen=True)
class World:
    viewer: User
    target: User
    space: FamilySpace
    fact: SourceFact
    job: StewardJob
    binding: steward_pipeline.Binding


def world(session: Session) -> World:
    viewer, space = create_agent_fixture(session, name="fenced-snapshot")
    target = create_user_with_pin(session, "fenced-parent", "123456", gender="f")
    create_space_member(session, space.id, target.id)
    space.kind = "lineage"
    viewer.gender = "m"
    session.commit()
    fact = create_source_fact(
        session,
        fact_type="biological_parent",
        subject_user_id=target.id,
        object_user_id=viewer.id,
        provenance="manual_entry",
        space_id=space.id,
    )
    transition_source_fact(session, fact, "confirm")
    session.commit()
    job = steward.lease_next_steward_job(
        session, leased_by="snapshot-owner", space_id=space.id, ttl_seconds=120
    )
    assert job is not None
    binding = steward_pipeline.binding_for(job)
    job = steward_pipeline.begin_job(session, binding)
    session.rollback()
    return World(viewer, target, space, fact, job, binding)


def pending_view(session: Session, fixture: World):
    versions = steward_snapshot.input_versions(session, fixture.space.id)
    session.rollback()
    snapshot = steward_snapshot.read_viewer(
        session.get_bind(),
        space_id=fixture.space.id,
        account_id=fixture.viewer.account.id,
        expected_versions=versions,
    )
    now = utcnow()
    generation = StewardGeneration(
        space_id=fixture.space.id,
        job_id=fixture.job.id,
        status="running",
        execution_cursor=fixture.job.trigger_cursor,
        input_versions_json=versions,
        lease_owner=fixture.binding.owner,
        lease_attempt=fixture.binding.attempt,
        valid_until=now + timedelta(minutes=1),
        manifest_sealed=True,
        required_views=1,
        created_at=now,
        updated_at=now,
    )
    session.add(generation)
    session.commit()
    work = steward_pipeline._stage_view(
        session.get_bind(),
        fixture.binding,
        generation.id,
        snapshot=snapshot,
        demand_revision=0,
    )
    assert work is not None
    return generation, work, snapshot


def test_explicit_read_snapshot_keeps_versions_and_inputs_together(db_session) -> None:
    fixture = world(db_session)
    fact_id, target_id = fixture.fact.id, fixture.target.id
    original_revision = fixture.fact.revision
    bind = db_session.get_bind()
    db_session.rollback()
    with steward_snapshot.read_transaction(bind) as reader:
        before = steward_snapshot.input_versions(reader, fixture.space.id)
        assert (
            reader.scalar(select(SourceFact.revision).where(SourceFact.id == fact_id))
            == original_revision
        )
        with Session(bind=bind) as writer:
            writer.execute(
                update(SourceFact)
                .where(SourceFact.id == fact_id)
                .values(revision=original_revision + 1)
            )
            writer.execute(update(User).where(User.id == target_id).values(gender="m"))
            writer.commit()
        # The second connection has committed; later SELECTs in the first
        # snapshot must still observe both original inputs and their version.
        assert (
            reader.scalar(select(SourceFact.revision).where(SourceFact.id == fact_id))
            == original_revision
        )
        assert reader.scalar(select(User.gender).where(User.id == target_id)) == "f"
        assert steward_snapshot.input_versions(reader, fixture.space.id) == before
    with steward_snapshot.read_transaction(bind) as reader:
        assert reader.scalar(select(User.gender).where(User.id == target_id)) == "m"
        assert not steward_snapshot.versions_match(
            reader, space_id=fixture.space.id, expected=before
        )


@pytest.mark.parametrize("change", ["fact", "membership", "gender", "birth", "name"])
def test_changed_input_rejects_completed_old_target_without_advancing_progress(
    db_session, change: str
) -> None:
    fixture = world(db_session)
    generation, work, snapshot = pending_view(db_session, fixture)
    resolution = resolve_graph(snapshot.graph, target_user_id=fixture.target.id)
    assert resolution.found
    bind = db_session.get_bind()
    with Session(bind=bind) as writer:
        if change == "fact":
            writer.execute(
                update(SourceFact)
                .where(SourceFact.id == fixture.fact.id)
                .values(revision=SourceFact.revision + 1)
            )
        elif change == "membership":
            writer.execute(
                update(SpaceMember)
                .where(
                    SpaceMember.space_id == fixture.space.id,
                    SpaceMember.user_id == fixture.target.id,
                )
                .values(status="removed")
            )
        else:
            value = {
                "gender": "m",
                "birth": {"cal_type": "solar", "date": "1950-01-01"},
                "name": "changed-display",
            }[change]
            writer.execute(
                update(User).where(User.id == fixture.target.id).values(**{change: value})
            )
        writer.commit()
    with pytest.raises(steward_snapshot.SnapshotChanged):
        steward_pipeline.save_target(
            bind,
            fixture.binding,
            generation.id,
            work.view_id,
            snapshot=snapshot,
            resolution=resolution,
        )
    with Session(bind=bind) as reader:
        saved = reader.get(StewardGenerationView, work.view_id)
        assert saved is not None and saved.completed_count == 0 and saved.status == "pending"
        target = reader.scalar(
            select(StewardViewTarget).where(StewardViewTarget.view_id == work.view_id)
        )
        assert target is not None and target.status == "pending" and target.resolution_json is None
        current = reader.get(StewardJob, fixture.job.id)
        assert current is not None and current.last_event_cursor is None
        assert reader.get(StewardPublication, fixture.space.id) is None


def test_duplicate_complete_target_only_counts_once(db_session) -> None:
    fixture = world(db_session)
    generation, work, snapshot = pending_view(db_session, fixture)
    resolution = resolve_graph(snapshot.graph, target_user_id=fixture.target.id)
    for _ in range(2):
        steward_pipeline.save_target(
            db_session.get_bind(),
            fixture.binding,
            generation.id,
            work.view_id,
            snapshot=snapshot,
            resolution=resolution,
        )
    with Session(bind=db_session.get_bind()) as reader:
        view = reader.get(StewardGenerationView, work.view_id)
        current_generation = reader.get(StewardGeneration, generation.id)
        assert view is not None and view.completed_count == view.total_count == 1
        assert view.revision == 2 and view.status == "ready"
        assert current_generation is not None and current_generation.ready_views == 1
        assert (
            reader.scalar(
                select(func.count())
                .select_from(StewardViewTarget)
                .where(StewardViewTarget.view_id == work.view_id)
            )
            == 1
        )
        assert reader.get(StewardPublication, fixture.space.id) is None


@pytest.mark.parametrize("same_owner", [True, False])
def test_old_attempt_cannot_heartbeat_fail_or_write_a_new_owner(
    db_session, same_owner: bool
) -> None:
    fixture = world(db_session)
    generation, work, snapshot = pending_view(db_session, fixture)
    resolution = resolve_graph(snapshot.graph, target_user_id=fixture.target.id)
    next_owner = fixture.binding.owner if same_owner else "replacement-owner"
    next_attempt = fixture.binding.attempt + 1
    deadline = utcnow() + timedelta(minutes=3)
    with Session(bind=db_session.get_bind()) as successor:
        successor.execute(
            update(StewardJob)
            .where(StewardJob.id == fixture.job.id)
            .values(
                attempt=next_attempt,
                leased_by=next_owner,
                lease_expires_at=deadline,
                status="running",
            )
        )
        successor.execute(
            update(StewardGeneration)
            .where(StewardGeneration.id == generation.id)
            .values(lease_attempt=next_attempt, lease_owner=next_owner)
        )
        successor.commit()
    # Keep the caller's old Session/ORM instances alive deliberately.
    with pytest.raises(HTTPException) as denied:
        steward_pipeline.heartbeat(db_session, fixture.binding, ttl=600)
    assert denied.value.status_code == 409
    steward_pipeline.record_failure(
        db_session, fixture.binding, error_code="late_failure", retryable=True
    )
    with pytest.raises(HTTPException):
        steward_pipeline.save_target(
            db_session.get_bind(),
            fixture.binding,
            generation.id,
            work.view_id,
            snapshot=snapshot,
            resolution=resolution,
        )
    with Session(bind=db_session.get_bind()) as reader:
        current = reader.get(StewardJob, fixture.job.id)
        current_generation = reader.get(StewardGeneration, generation.id)
        assert current is not None and current.status == "running"
        assert current.attempt == next_attempt and current.leased_by == next_owner
        assert current.lease_expires_at == deadline and current.error_code is None
        assert current_generation is not None and current_generation.status == "running"
        assert current_generation.error_code is None and current_generation.ready_views == 0


def test_expired_attempt_does_not_resurrect_itself(db_session) -> None:
    fixture = world(db_session)
    generation, _work, _snapshot = pending_view(db_session, fixture)
    deadline = utcnow() - timedelta(seconds=1)
    with Session(bind=db_session.get_bind()) as writer:
        writer.execute(
            update(StewardJob)
            .where(StewardJob.id == fixture.job.id)
            .values(lease_expires_at=deadline)
        )
        writer.commit()
    with pytest.raises(HTTPException):
        steward_pipeline.heartbeat(db_session, fixture.binding, ttl=600)
    steward_pipeline.record_failure(
        db_session, fixture.binding, error_code="late_failure", retryable=True
    )
    with Session(bind=db_session.get_bind()) as reader:
        current = reader.get(StewardJob, fixture.job.id)
        assert (
            current is not None
            and current.status == "running"
            and current.lease_expires_at == deadline
        )
        current_generation = reader.get(StewardGeneration, generation.id)
        assert current_generation is not None and current_generation.status == "running"


def test_publication_failure_rolls_back_pointer_cursor_and_delivery_activation(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = world(db_session)
    monkeypatch.setattr(
        steward_runtime, "run_slice", lambda state: advance_search(state, max_expansions=8)
    )
    monkeypatch.setattr(steward_runtime, "is_stopping", lambda: False)
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    demand = StewardViewDemand(
        space_id=fixture.space.id,
        viewer_account_id=fixture.viewer.account.id,
        revision=1,
        fulfilled_revision=0,
        requested_at=utcnow(),
    )
    db_session.add(demand)
    db_session.commit()
    upper = fixture.job.trigger_cursor
    summary = steward_pipeline.execute(db_session, fixture.job, now=utcnow(), upper=upper)
    generation_id = summary["generation_id"]
    bind = db_session.get_bind()
    with Session(bind=bind) as reader:
        assert reader.get(StewardPublication, fixture.space.id) is None
        assert reader.get(StewardViewDemand, demand.id).fulfilled_revision == 0
        assert reader.scalar(select(func.count()).select_from(ActionCard)) == 0
        assert reader.scalar(select(func.count()).select_from(Notification)) == 0
        assert steward_delivery.backlog(reader) == {}
        planned = reader.scalar(
            select(func.count())
            .select_from(StewardDeliveryIntent)
            .where(StewardDeliveryIntent.generation_id == generation_id)
        )
        assert planned and planned > 0

    # Inject after the publication pointer and cursor have been flushed, while
    # still inside their transaction. Only staging may survive this failure.
    def fail_completed_event(session, **kwargs):
        assert kwargs["event_type"] == "steward.job_completed"
        assert session.get(StewardPublication, fixture.space.id) is not None
        raise RuntimeError("injected publication failure")

    with monkeypatch.context() as failed_publish:
        failed_publish.setattr(domain_events, "emit", fail_completed_event)
        with pytest.raises(RuntimeError, match="injected publication failure"):
            steward_pipeline.publish(db_session, fixture.binding, summary=summary, upper=upper)
    with Session(bind=bind) as reader:
        assert reader.get(StewardPublication, fixture.space.id) is None
        assert reader.get(StewardViewDemand, demand.id).fulfilled_revision == 0
        current = reader.get(StewardJob, fixture.job.id)
        assert (
            current is not None
            and current.status == "running"
            and current.last_event_cursor is None
        )
        generation = reader.get(StewardGeneration, generation_id)
        assert generation is not None and generation.status == "running"
        assert steward_delivery.backlog(reader) == {}
        assert (
            reader.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.type == "steward.job_completed")
            )
            == 0
        )
    steward_pipeline.publish(db_session, fixture.binding, summary=summary, upper=upper)
    with Session(bind=bind) as reader:
        publication = reader.get(StewardPublication, fixture.space.id)
        current = reader.get(StewardJob, fixture.job.id)
        assert publication is not None and publication.generation_id == generation_id
        assert reader.get(StewardViewDemand, demand.id).fulfilled_revision == 1
        assert (
            current is not None
            and current.status == "succeeded"
            and current.last_event_cursor == upper
        )
        assert steward_delivery.backlog(reader) == {"pending": planned}
        assert (
            reader.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.type == "steward.job_completed")
            )
            == 1
        )
    with pytest.raises(HTTPException):
        steward_pipeline.publish(db_session, fixture.binding, summary=summary, upper=upper)
