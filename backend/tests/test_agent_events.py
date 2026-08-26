"""Agent Run 公开事件流测试：幂等追加、seq 连续性、未知类型拒绝、leased→running 提升。"""

import pytest
from conftest import create_agent_fixture, create_agent_message, create_agent_session
from fastapi import HTTPException
from sqlalchemy import select

from app.models.agent import AgentJob, AgentRunEvent
from app.services import agent_events, agent_queue
from app.services.agent_events import EventEntry


def _enqueue(db, session):
    message = create_agent_message(db, session)
    return agent_queue.enqueue_run(
        db,
        agent_session=session,
        kind="assistant",
        policy_version="p",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )


def _error(exc: Exception) -> dict:
    detail = getattr(exc, "detail", None)
    assert isinstance(detail, dict) and "__api_error__" in detail
    return detail["__api_error__"]  # type: ignore[no-any-return]


def test_append_assigns_ids_and_persists_in_order(db_session):
    user, space = create_agent_fixture(db_session, name="ev1")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    accepted, duplicates = agent_events.append_events(
        db_session,
        run,
        [
            EventEntry(seq=1, type="turn.started", public_payload={"turn": 1}),
            EventEntry(seq=2, type="turn.completed", public_payload={"turn": 1}),
        ],
    )
    db_session.commit()
    assert [row.seq for row in accepted] == [1, 2]
    assert duplicates == []
    rows = list(
        db_session.scalars(
            select(AgentRunEvent).where(AgentRunEvent.run_id == run.id).order_by(AgentRunEvent.seq)
        )
    )
    assert [r.seq for r in rows] == [0, 1, 2]  # seq0 为入队首个事件


def test_duplicate_retry_is_idempotent(db_session):
    user, space = create_agent_fixture(db_session, name="ev2")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    entry = EventEntry(seq=1, type="turn.started", public_payload={"turn": 1})
    first, _ = agent_events.append_events(db_session, run, [entry])
    accepted, duplicates = agent_events.append_events(db_session, run, [entry])
    assert accepted == [] and duplicates == [1]
    # 完全一致的重试不产生第二行
    rows = list(db_session.scalars(select(AgentRunEvent.id).where(AgentRunEvent.run_id == run.id)))
    assert len(rows) == 2


def test_same_seq_different_content_conflicts(db_session):
    user, space = create_agent_fixture(db_session, name="ev3")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    agent_events.append_events(
        db_session, run, [EventEntry(seq=1, type="turn.started", public_payload={"turn": 1})]
    )
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session,
            run,
            [EventEntry(seq=1, type="turn.started", public_payload={"turn": 2})],
        )
    err = _error(exc_info.value)
    assert err["code"] == "AGENT_EVENT_SEQ_CONFLICT"


def test_gap_or_regression_rejected(db_session):
    user, space = create_agent_fixture(db_session, name="ev4")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session, run, [EventEntry(seq=5, type="turn.started", public_payload={})]
        )
    assert _error(exc_info.value)["code"] == "AGENT_EVENT_SEQ_CONFLICT"
    # 回退同样拒绝
    agent_events.append_events(
        db_session, run, [EventEntry(seq=1, type="turn.started", public_payload={})]
    )
    with pytest.raises(HTTPException):
        agent_events.append_events(
            db_session, run, [EventEntry(seq=1, type="turn.completed", public_payload={})]
        )


def test_unknown_type_rejected_without_persist(db_session):
    user, space = create_agent_fixture(db_session, name="ev5")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session,
            run,
            [EventEntry(seq=1, type="card.render.v2", public_payload={})],
        )
    assert _error(exc_info.value)["code"] == "AGENT_EVENT_INVALID"
    max_seq = db_session.scalar(
        select(AgentRunEvent.seq)
        .where(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.seq.desc())
        .limit(1)
    )
    assert max_seq == 0  # 只有入队 seq0，非法类型未落公开流


def test_run_started_promotes_leased_to_running(db_session):
    user, space = create_agent_fixture(db_session, name="ev6")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.run.status == "leased"
    agent_events.append_events(
        db_session, grant.run, [EventEntry(seq=1, type="run.started", public_payload={})]
    )
    assert grant.run.status == "running"
    job = db_session.get(AgentJob, grant.job.id)
    assert job is not None and job.status == "running"

    # queued 状态直接发 run.started 属协议违规（FSM fail-closed）
    other = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    queued_run = _enqueue(db_session, other)
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session, queued_run, [EventEntry(seq=1, type="run.started", public_payload={})]
        )
    assert _error(exc_info.value)["code"] == "AGENT_RUN_NOT_RUNNING"
