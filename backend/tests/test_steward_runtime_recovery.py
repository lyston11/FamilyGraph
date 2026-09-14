"""Cross-space scheduling and recovery from a lost partial CPU continuation."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import config
from app.models.steward import (
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardRetryBudget,
    StewardViewTarget,
)
from app.models.v2_foundation import DomainEvent
from app.services import steward, steward_pipeline, steward_runtime
from app.services.relationship_resolver import advance_search
from app.services.source_facts import create_source_fact, transition_source_fact
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_space_member, create_user_with_pin


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_WORKER_ENABLED", True)


def family(session, name, size):
    viewer, space = create_agent_fixture(session, name=name)
    people = [viewer]
    for index in range(1, size):
        parent = create_user_with_pin(session, f"{name}-{index}", "123456", gender="m")
        create_space_member(session, space.id, parent.id)
        fact = create_source_fact(
            session,
            fact_type="biological_parent",
            subject_user_id=parent.id,
            object_user_id=people[-1].id,
            provenance="manual_entry",
            space_id=space.id,
        )
        transition_source_fact(session, fact, "confirm")
        people.append(parent)
    session.commit()
    return space.id


def wait_for_publication(bind, space_id, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with Session(bind=bind) as reader:
            publication = reader.get(StewardPublication, space_id)
            if publication is not None:
                return publication.generation_id
        time.sleep(0.02)
    raise AssertionError(f"space {space_id} did not publish within the test deadline")


def test_small_space_publishes_while_large_space_has_a_yielded_cpu_target(db_session, monkeypatch):
    large = family(db_session, "fair-large", 6)
    small = family(db_session, "fair-small", 2)
    bind = db_session.get_bind()
    yielded, resume = threading.Event(), threading.Event()
    real_slice = steward_runtime.run_slice
    monkeypatch.setattr(config, "STEWARD_SEARCH_SLICE_EXPANSIONS", 1)

    def observe_slice(state):
        # Exercise the actual single spawn worker. Hold the large coordinator
        # only after its CPU slice has yielded, so another space can use it.
        result = real_slice(state)
        if state.graph.space_id == large and not yielded.is_set():
            assert result.resolution is None and result.state.expansions > 0
            yielded.set()
            assert resume.wait(20), "test did not release the yielded coordinator"
        return result

    monkeypatch.setattr(steward_runtime, "run_slice", observe_slice)
    assert steward_runtime.launch_due(space_id=large, limit=1) == 1
    try:
        assert yielded.wait(10), "large job did not yield its first real CPU slice"
        assert steward_runtime.launch_due(space_id=small, limit=1) == 1
        wait_for_publication(bind, small)
        with Session(bind=bind) as reader:
            assert reader.get(StewardPublication, large) is None
            large_job = reader.scalar(select(StewardJob).where(StewardJob.space_id == large))
            assert large_job.status == "running" and large_job.last_event_cursor is None
    finally:
        resume.set()
    wait_for_publication(bind, large)


class SimulatedProcessLoss(BaseException):
    """Bypass normal Exception settlement, like a coordinator process exiting."""


def test_new_attempt_reuses_committed_target_and_restarts_lost_partial_target(
    db_session, monkeypatch
):
    space_id = family(db_session, "restart-family", 3)
    bind = db_session.get_bind()
    grant = steward.lease_next_steward_job(db_session, leased_by="lost-process", space_id=space_id)
    assert grant is not None
    job_id, old_attempt, cursor = grant.id, grant.attempt, grant.trigger_cursor
    old_binding = steward_pipeline.binding_for(grant)
    committed = []
    partial_pair = None
    save = steward_pipeline.save_target

    def save_and_observe(*args, **kwargs):
        save(*args, **kwargs)
        # Retain one detached old receipt solely to exercise a late worker.
        if not committed:
            committed.append((args, kwargs))

    def crash_after_partial(state):
        nonlocal partial_pair
        if committed:
            partial = advance_search(state, max_expansions=1)
            assert partial.resolution is None and partial.state.expansions > 0
            partial_pair = (state.graph.viewer_user_id, state.target_user_id)
            raise SimulatedProcessLoss
        return advance_search(state)

    monkeypatch.setattr(steward_pipeline, "save_target", save_and_observe)
    monkeypatch.setattr(steward_runtime, "run_slice", crash_after_partial)
    with pytest.raises(SimulatedProcessLoss):
        steward.execute_steward_job(
            db_session,
            grant,
            worker_id="lost-process",
            expected_attempt=old_attempt,
            drain_delivery=False,
        )
    assert committed and partial_pair is not None
    old_args, old_kwargs = committed[0]
    old_generation_id = old_args[2]
    completed = old_kwargs["resolution"]
    completed_pair = (completed.viewer_user_id, completed.target_user_id)
    assert partial_pair != completed_pair
    db_session.rollback()
    with Session(bind=bind) as reader:
        assert reader.get(StewardPublication, space_id) is None
        assert reader.get(StewardJob, job_id).last_event_cursor is None
        abandoned = reader.scalar(
            select(StewardRetryBudget).where(
                StewardRetryBudget.space_id == space_id,
                StewardRetryBudget.attempts > 0,
            )
        )
        assert abandoned is not None and abandoned.attempts == 1
        budget_id, budget_limit = abandoned.id, abandoned.max_attempts
        assert (
            reader.scalar(
                select(func.count())
                .select_from(StewardViewTarget)
                .where(StewardViewTarget.status == "ready")
            )
            == 1
        )

    # No old Session/search continuation participates in recovery. Expiry is
    # written by an independent connection and the normal reaper requeues it.
    with Session(bind=bind) as recovery:
        recovery.execute(
            update(StewardJob)
            .where(StewardJob.id == job_id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )
        recovery.commit()
        assert steward.reaper_pass(recovery) == 1
        new = steward.lease_next_steward_job(
            recovery, leased_by="replacement-process", space_id=space_id
        )
        assert new is not None and new.id == job_id and new.attempt == old_attempt + 1
        budget = recovery.get(StewardRetryBudget, budget_id)
        assert budget.attempts == 1 and budget.max_attempts == budget_limit
        with pytest.raises(HTTPException):
            save(*old_args, **old_kwargs)
        with pytest.raises(HTTPException):
            steward_pipeline.heartbeat(recovery, old_binding, ttl=120)

    searched = []

    def fresh_search(state):
        pair = (state.graph.viewer_user_id, state.target_user_id)
        assert pair != completed_pair, "committed target was searched again after restart"
        if pair == partial_pair:
            assert state.expansions == 0, "a lost partial continuation was treated as complete"
            with Session(bind=bind) as reader:
                assert reader.get(StewardRetryBudget, budget_id).attempts == 2
        searched.append(pair)
        return advance_search(state)

    monkeypatch.setattr(steward_pipeline, "save_target", save)
    monkeypatch.setattr(steward_runtime, "run_slice", fresh_search)
    with Session(bind=bind) as replacement:
        current = replacement.get(StewardJob, job_id)
        summary = steward.execute_steward_job(
            replacement,
            current,
            worker_id="replacement-process",
            expected_attempt=old_attempt + 1,
            drain_delivery=False,
        )
    assert partial_pair in searched and completed_pair not in searched
    with Session(bind=bind) as reader:
        publication = reader.get(StewardPublication, space_id)
        assert publication is not None and publication.generation_id == summary["generation_id"]
        assert publication.generation_id != old_generation_id
        assert reader.get(StewardGeneration, old_generation_id).status == "superseded"
        current = reader.get(StewardJob, job_id)
        assert current.status == "succeeded" and current.last_event_cursor == cursor
        # Success refunds only the replacement's reservation, not the lost
        # process's attempt. Restart never resets the input-scoped budget.
        budget = reader.get(StewardRetryBudget, budget_id)
        assert budget.attempts == 1 and budget.max_attempts == budget_limit
        assert (
            reader.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.type == "steward.job_completed")
            )
            == 1
        )
        rows = reader.scalars(
            select(StewardGenerationView).where(
                StewardGenerationView.generation_id == publication.generation_id
            )
        ).all()
        assert len(rows) == 3 and all(row.status == "ready" for row in rows)
        assert sum(row.completed_count for row in rows) == 6
