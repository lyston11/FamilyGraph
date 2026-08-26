"""Agent Runtime 队列/FSM 测试（RT-2 并发约束、RT-7 恢复语义、scope 不可变）。"""

from datetime import timedelta

import pytest
from conftest import create_agent_fixture, create_agent_message, create_agent_session
from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.models.agent import AgentJob, AgentRun, AgentRunEvent
from app.services import agent_queue
from app.utils import timeutil


def _enqueue(db, agent_session, *, kind="assistant", message=None, allowlist=None):
    return agent_queue.enqueue_run(
        db,
        agent_session=agent_session,
        kind=kind,
        policy_version="test-policy-1",
        tool_allowlist=allowlist or ["familygraph.echo"],
        message=message,
    )


def _error_code(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and "__api_error__" in detail:
        return str(detail["__api_error__"]["code"])
    raise AssertionError(f"unexpected exception: {exc!r}")


def test_enqueue_creates_run_job_and_first_event(db_session):
    user, space = create_agent_fixture(db_session, name="enqueue")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db_session, session)
    run = _enqueue(db_session, session, message=message)

    assert run.status == "queued"
    assert run.attempt == 0
    assert run.job_id is not None
    job = db_session.get(AgentJob, run.job_id)
    assert job is not None and job.run_id == run.id and job.status == "queued"
    event = db_session.scalar(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id))
    assert event is not None
    assert (event.seq, event.type) == (0, "message.user_added")
    assert event.public_payload["message_id"] == message.id


def test_session_single_active_run_conflict(db_session):
    user, space = create_agent_fixture(db_session, name="busy")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    with pytest.raises(HTTPException) as exc_info:
        _enqueue(db_session, session)
    assert _error_code(exc_info.value) == "AGENT_RUN_SESSION_BUSY"


def test_account_assistant_run_cap_two(db_session):
    owner, space = create_agent_fixture(db_session, name="cap")
    s1 = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    s2 = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    s3 = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    _enqueue(db_session, s1)
    _enqueue(db_session, s2)
    with pytest.raises(HTTPException) as exc_info:
        _enqueue(db_session, s3)
    assert _error_code(exc_info.value) == "AGENT_RUN_ACCOUNT_LIMIT"


def test_steward_runs_do_not_consume_assistant_quota(db_session):
    owner, space = create_agent_fixture(db_session, name="quota")
    steward_session = create_agent_session(
        db_session, account_id=owner.account.id, space_id=space.id, kind="steward"
    )
    _enqueue(db_session, steward_session, kind="steward")
    assistant_session = create_agent_session(
        db_session, account_id=owner.account.id, space_id=space.id
    )
    run = _enqueue(db_session, assistant_session)
    assert run.kind == "assistant"


def test_steward_space_single_active_job(db_session):
    owner_a, space = create_agent_fixture(db_session, name="sa")
    owner_b, _ = create_agent_fixture(db_session, name="sb")
    st_a = create_agent_session(
        db_session, account_id=owner_a.account.id, space_id=space.id, kind="steward"
    )
    st_b = create_agent_session(
        db_session, account_id=owner_b.account.id, space_id=space.id, kind="steward"
    )
    _enqueue(db_session, st_a, kind="steward")
    with pytest.raises(HTTPException) as exc_info:
        _enqueue(db_session, st_b, kind="steward")
    assert _error_code(exc_info.value) == "AGENT_STEWARD_SPACE_BUSY"


def test_lease_fifo_attempt_and_leased_by(db_session):
    user, space = create_agent_fixture(db_session, name="lease")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sidecar-a")
    assert grant is not None
    assert grant.job.status == "leased"
    assert grant.job.attempt == 1 and grant.job.leased_by == "sidecar-a"
    assert grant.run.status == "leased" and grant.run.attempt == 1
    # 无更多可租
    assert agent_queue.lease_next(db_session, kind="assistant", leased_by="sidecar-b") is None


def test_lease_without_kind_fifo_across_queues(db_session):
    """kind=None：跨队列按 created_at FIFO 取任意 queued（不限定 kind）。"""
    owner_a, space_a = create_agent_fixture(db_session, name="nk-a")
    owner_b, space_b = create_agent_fixture(db_session, name="nk-b")
    assistant_session = create_agent_session(
        db_session, account_id=owner_a.account.id, space_id=space_a.id
    )
    steward_session = create_agent_session(
        db_session, account_id=owner_b.account.id, space_id=space_b.id, kind="steward"
    )
    steward_run = _enqueue(db_session, steward_session, kind="steward")
    assistant_run = _enqueue(db_session, assistant_session)

    grant = agent_queue.lease_next(db_session, kind=None, leased_by="sc")
    assert grant is not None
    assert grant.job.kind == "steward" and grant.job.run_id == steward_run.id

    grant = agent_queue.lease_next(db_session, kind=None, leased_by="sc")
    assert grant is not None
    assert grant.job.kind == "assistant" and grant.job.run_id == assistant_run.id
    assert agent_queue.lease_next(db_session, kind=None, leased_by="sc") is None


def test_lease_without_kind_assistant_first_on_created_at_tie(db_session):
    """kind=None 且 created_at 并列：assistant 先于 steward（仅作确定性排序）。"""
    owner_a, space_a = create_agent_fixture(db_session, name="tie-a")
    owner_b, space_b = create_agent_fixture(db_session, name="tie-b")
    assistant_session = create_agent_session(
        db_session, account_id=owner_a.account.id, space_id=space_a.id
    )
    steward_session = create_agent_session(
        db_session, account_id=owner_b.account.id, space_id=space_b.id, kind="steward"
    )
    steward_run = _enqueue(db_session, steward_session, kind="steward")  # 更早入队、id 更小
    assistant_run = _enqueue(db_session, assistant_session)

    moment = timeutil.utcnow()
    db_session.execute(
        update(AgentJob)
        .where(AgentJob.id.in_([steward_run.job_id, assistant_run.job_id]))
        .values(created_at=moment)
    )
    db_session.commit()
    db_session.expire_all()

    grant = agent_queue.lease_next(db_session, kind=None, leased_by="sc")
    assert grant is not None
    assert grant.job.kind == "assistant"


def test_heartbeat_extends_and_rejects_terminal(db_session):
    user, space = create_agent_fixture(db_session, name="hb")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None
    job = grant.job
    expires_1 = agent_queue.heartbeat(db_session, job, ttl_seconds=600)
    assert job.status == "leased" and job.lease_expires_at == expires_1
    assert run.lease_expires_at == expires_1  # heartbeat 打在 job 同步更新 run
    agent_queue.settle_run(db_session, run, status="failed", error_code="X")
    with pytest.raises(HTTPException) as exc_info:
        agent_queue.heartbeat(db_session, job)
    assert _error_code(exc_info.value) == "AGENT_JOB_NOT_ACTIVE"


def test_reaper_returns_to_queue_then_expires_after_attempts(db_session):
    user, space = create_agent_fixture(db_session, name="reap")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session, allowlist=["familygraph.echo"])

    for expected_attempt in (1, 2):
        grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
        assert grant is not None and grant.job.attempt == expected_attempt
        # 模拟 lease 过期（crash / 心跳停止）
        past = timeutil.utcnow()
        grant.job.lease_expires_at = past
        grant.run.lease_expires_at = past
        db_session.commit()
        handled = agent_queue.reaper_pass(db_session)
        assert handled == 1
        assert grant.job.status == "queued" and grant.run.status == "queued"

    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.job.attempt == 3
    past = timeutil.utcnow()
    grant.job.lease_expires_at = past
    grant.run.lease_expires_at = past
    db_session.commit()
    agent_queue.reaper_pass(db_session)
    assert grant.job.status == "expired" and grant.run.status == "expired"
    assert grant.run.error_code == "AGENT_LEASE_EXPIRED"
    # 终态不再可租，reaper 不再处理
    assert agent_queue.lease_next(db_session, kind="assistant", leased_by="sc") is None
    assert agent_queue.reaper_pass(db_session) == 0


def test_settle_and_cancel_terminal_immutability(db_session):
    user, space = create_agent_fixture(db_session, name="term")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None  # settle 仅可从 leased/running 进入终态
    settled = agent_queue.settle_run(db_session, run, status="succeeded")
    assert settled.status == "succeeded" and settled.settled_at is not None
    terminal_event = db_session.scalar(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.seq.desc())
    )
    assert terminal_event is not None and terminal_event.type == "run.settled"
    with pytest.raises(HTTPException) as exc_info:
        agent_queue.settle_run(db_session, run, status="failed")
    assert _error_code(exc_info.value) == "AGENT_RUN_TERMINAL"
    with pytest.raises(HTTPException) as exc_info:
        agent_queue.cancel_run(db_session, run)
    assert _error_code(exc_info.value) == "AGENT_RUN_TERMINAL"


def test_cancel_from_queued_writes_event(db_session):
    user, space = create_agent_fixture(db_session, name="cancel")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    cancelled = agent_queue.cancel_run(db_session, run)
    assert cancelled.status == "cancelled"
    job = db_session.get(AgentJob, run.job_id)
    assert job is not None and job.status == "cancelled"
    event = db_session.scalar(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.seq.desc())
    )
    assert event is not None and event.type == "run.cancelled"


def test_prune_finished_removes_only_old_terminal_runs(db_session):
    user, space = create_agent_fixture(db_session, name="prune")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    old_run = _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None  # settle 仅可从 leased/running 进入终态
    agent_queue.settle_run(db_session, old_run, status="succeeded")

    other = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    active_run = _enqueue(db_session, other)

    cutoff = timeutil.utcnow()
    old_run.settled_at = cutoff - timedelta(seconds=60)  # 早于阈值的终态 run 才可清理
    db_session.commit()
    removed = agent_queue.prune_finished(db_session, older_than=cutoff)
    assert removed >= 1
    db_session.expire_all()  # 核心删除不经过 ORM，强制回库验证真实状态
    assert db_session.get(AgentRun, old_run.id) is None
    assert db_session.get(AgentJob, old_run.job_id) is None  # CASCADE 清除
    assert (
        db_session.scalar(
            select(AgentRunEvent.id).where(AgentRunEvent.run_id == old_run.id).limit(1)
        )
        is None
    )  # 事件级联清除
    assert db_session.get(AgentRun, active_run.id) is not None  # 活跃 run 不受影响


def test_session_scope_immutable_by_trigger(db_session):
    """scope 三元组创建后不可变：DB trigger 强制（服务层无更新路径）。"""
    user, space = create_agent_fixture(db_session, name="immu")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    other_space = create_agent_fixture(db_session, name="immu2")[1]
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE agent_sessions SET space_id = :sid WHERE id = :id"),
            {"sid": other_space.id, "id": session.id},
        )


def test_enqueue_invalid_kind_rejected_by_check_constraint(db_session):
    """非法 kind 由 CHECK 兑底拒绝（未知枚举 fail-closed，非业务错误结构）。"""
    from sqlalchemy.exc import IntegrityError

    user, space = create_agent_fixture(db_session, name="kindchk")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    with pytest.raises(IntegrityError):
        _enqueue(db_session, session, kind="wizard")
