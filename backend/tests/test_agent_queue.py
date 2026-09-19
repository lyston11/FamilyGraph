"""Agent Runtime 队列/FSM 测试（RT-2 并发约束、RT-7 恢复语义、scope 不可变）。"""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models.agent import AgentJob, AgentRun, AgentRunEvent
from app.models.audit_log import AuditLog
from app.services import agent_queue
from app.utils import timeutil
from conftest import create_agent_fixture, create_agent_message, create_agent_session


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


def test_unsupported_kind_is_rejected_before_queue_write(db_session):
    owner, space = create_agent_fixture(db_session, name="kind")
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)

    with pytest.raises(HTTPException) as exc_info:
        _enqueue(db_session, session, kind="steward")

    assert _error_code(exc_info.value) == "AGENT_KIND_UNSUPPORTED"
    assert db_session.scalar(select(AgentRun).where(AgentRun.session_id == session.id)) is None
    with pytest.raises(HTTPException) as exc_info:
        agent_queue.lease_next(db_session, kind="steward", leased_by="sc")
    assert _error_code(exc_info.value) == "AGENT_KIND_UNSUPPORTED"


def test_lease_fifo_attempt_and_leased_by(db_session):
    user, space = create_agent_fixture(db_session, name="lease")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sidecar-a")
    assert grant is not None
    assert grant.job.status == "leased"
    assert grant.job.attempt == 1 and grant.job.leased_by == "sidecar-a"
    assert grant.run.status == "leased" and grant.run.attempt == 1
    assert agent_queue.lease_next(db_session, kind="assistant", leased_by="sidecar-b") is None


def test_lease_without_kind_is_rejected_before_queue_read(db_session):
    owner, space = create_agent_fixture(db_session, name="missing-kind")
    session = create_agent_session(db_session, account_id=owner.account.id, space_id=space.id)
    _enqueue(db_session, session)

    with pytest.raises(HTTPException) as exc_info:
        agent_queue.lease_next(db_session, kind=None, leased_by="sc")
    assert _error_code(exc_info.value) == "AGENT_KIND_UNSUPPORTED"


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


def test_heartbeat_does_not_extend_cancel_requested_lease(db_session):
    """Cancellation fence prevents a healthy sidecar from keeping the lease alive."""
    user, space = create_agent_fixture(db_session, name="hb-cancel")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None
    original_expiry = grant.job.lease_expires_at
    run.cancel_requested = True
    grant.job.cancel_requested = True
    db_session.commit()

    expires = agent_queue.heartbeat(db_session, grant.job, ttl_seconds=3600)
    assert expires == original_expiry
    assert grant.job.lease_expires_at == original_expiry
    assert grant.run.lease_expires_at == original_expiry


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
    # 终态事件由服务端唯一写入（run.expired），且携带 error_code（R4）
    terminal = db_session.scalar(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == grant.run.id)
        .order_by(AgentRunEvent.seq.desc())
    )
    assert terminal is not None
    assert terminal.type == "run.expired"
    assert terminal.public_payload["error_code"] == "AGENT_LEASE_EXPIRED"
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


def test_reaper_converges_a_cancelled_run_without_waiting_for_lease_expiry(db_session):
    """取消是终态意图：不等 lease 自然过期就收敛。

    在途 sidecar 看到取消裁决后就不再写入也不结算（见 worker 的取消语义），
    若只靠 lease 过期收敛，浏览器会把取消后的等待一直转到租约结束（实测
    304s，而 AGENT_LEASE_TTL_SECONDS 默认 300）。
    """
    user, space = create_agent_fixture(db_session, name="reap-cancel")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.job.status == "leased"
    # 租约仍然健康（远未到期），但浏览器已请求取消。
    assert grant.job.lease_expires_at is not None
    assert grant.job.lease_expires_at > timeutil.utcnow()

    agent_queue.request_cancel(db_session, grant.run, actor_id=None)
    db_session.commit()

    assert agent_queue.reaper_pass(db_session) == 1
    db_session.expire_all()
    run = db_session.get(AgentRun, grant.run.id)
    job = db_session.get(AgentJob, grant.job.id)
    assert run is not None and run.status == "cancelled"
    assert job is not None and job.status == "cancelled"
    assert run.error_code is None
    terminal = db_session.scalar(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.seq.desc())
    )
    assert terminal is not None and terminal.type == "run.cancelled"
    audit_row = db_session.scalar(select(AuditLog).where(AuditLog.action == "agent_lease_expired"))
    assert audit_row is not None
    assert audit_row.detail["reason"] == "cancel_requested"


def test_reaper_still_waits_for_expiry_when_not_cancelled(db_session):
    """非取消的活跃租约不得被 reaper 提前回收（防止上面放宽条件后误伤）。"""
    user, space = create_agent_fixture(db_session, name="reap-live")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None

    assert agent_queue.reaper_pass(db_session) == 0
    db_session.expire_all()
    run = db_session.get(AgentRun, grant.run.id)
    assert run is not None and run.status == "leased"


def test_reaper_converges_a_revoked_membership_without_retrying(db_session):
    """执行身份被永久撤销：直接终态收敛，不回队重试（09-19 受控验收发现）。

    撤权后每次内部请求（含心跳）都被 ``_authorize_run`` 拒，sidecar 不再续租也
    不结算。旧逻辑把租约丢失当可重试，新 attempt 立刻又 403，直到 attempt 耗尽
    （约 3×300s）才收口 expired：用户看着假「生成中…」，失败分母还被记成租约超时。
    """
    from app.models.space import SpaceMember

    user, space = create_agent_fixture(db_session, name="reap-revoked")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.job.attempt == 1
    # 模拟 owner 撤权（真实路径是 space_members 的 FSM 转换）。
    member = db_session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == user.id)
    )
    assert member is not None
    member.status = "removed"
    db_session.commit()

    assert agent_queue.reaper_pass(db_session) == 1
    db_session.expire_all()
    run = db_session.get(AgentRun, grant.run.id)
    job = db_session.get(AgentJob, grant.job.id)
    assert run is not None and run.status == "failed"
    assert run.error_code == "AGENT_MEMBERSHIP_REVOKED"
    assert run.settled_at is not None
    assert job is not None and job.status == "failed"
    # 关键：没有回队重试（attempt 不增加、状态不是 queued）。
    assert job.attempt == 1
    terminal = db_session.scalar(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.seq.desc())
    )
    assert terminal is not None and terminal.type == "run.failed"
    assert terminal.public_payload["error_code"] == "AGENT_MEMBERSHIP_REVOKED"
    audit_row = db_session.scalar(select(AuditLog).where(AuditLog.action == "agent_lease_expired"))
    assert audit_row is not None
    assert audit_row.detail["reason"] == "membership_revoked"


def test_reaper_reports_revocation_even_when_attempts_exhausted(db_session):
    """attempt 已耗尽的撤权 Run 仍归因为撤权，不是租约超时。

    判定顺序：先看执行身份是否永久失效，再看 attempt。否则撤权会被记成
    ``expired``，运维会把它误读为 worker/机器问题。
    """
    from app.models.space import SpaceMember

    user, space = create_agent_fixture(db_session, name="reap-revoked-exhausted")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None
    grant.job.attempt = grant.job.max_attempts  # 重试已耗尽
    member = db_session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space.id, SpaceMember.user_id == user.id)
    )
    assert member is not None
    member.status = "removed"
    db_session.commit()

    assert agent_queue.reaper_pass(db_session) == 1
    db_session.expire_all()
    run = db_session.get(AgentRun, grant.run.id)
    assert run is not None and run.status == "failed"
    assert run.error_code == "AGENT_MEMBERSHIP_REVOKED"


def test_reaper_still_requeues_a_healthy_expired_lease(db_session):
    """成员资格仍有效的普通租约过期：保持既有回队语义（回归）。"""
    user, space = create_agent_fixture(db_session, name="reap-healthy")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None
    grant.job.lease_expires_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert agent_queue.reaper_pass(db_session) == 1
    db_session.expire_all()
    job = db_session.get(AgentJob, grant.job.id)
    run = db_session.get(AgentRun, grant.run.id)
    assert job is not None and job.status == "queued"
    assert run is not None and run.status == "queued"
    assert run.settled_at is None


def test_reaper_expires_a_healthy_lease_when_attempts_exhausted(db_session):
    """成员资格有效且 attempt 耗尽：仍是 ``expired``（回归既有语义）。"""
    user, space = create_agent_fixture(db_session, name="reap-healthy-exhausted")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None
    grant.job.attempt = grant.job.max_attempts
    grant.job.lease_expires_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert agent_queue.reaper_pass(db_session) == 1
    db_session.expire_all()
    run = db_session.get(AgentRun, grant.run.id)
    assert run is not None and run.status == "expired"
    assert run.error_code == "AGENT_LEASE_EXPIRED"


def test_cancel_then_failed_settle_keeps_failed(db_session):
    """取消已请求后 sidecar 结算 failed：真实故障不被吞成 cancelled。

    `_settle` 只把 succeeded 改判为 cancelled，failed 原样保留。否则用户取消
    一次正在失败（例如上游永久 4xx）的 run 后，会看不到真实错误，运维也失去
    故障分母。反向（取消 + succeeded → cancelled）见
    `test_cancel_running_then_settle_overrides_to_cancelled`。
    """
    user, space = create_agent_fixture(db_session, name="cancel-fail")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None

    agent_queue.request_cancel(db_session, grant.run, actor_id=None)
    db_session.commit()

    settled = agent_queue.settle_run(
        db_session, grant.run, status="failed", error_code="PROVIDER_STREAM_ERROR"
    )
    db_session.expire(settled)
    assert settled.status == "failed"
    assert settled.error_code == "PROVIDER_STREAM_ERROR"
    # 没有改判审计（只有 succeeded 分支写）。
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.action == "agent_run_settle_overridden"))
        is None
    )
    terminal = db_session.scalar(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == settled.id)
        .order_by(AgentRunEvent.seq.desc())
    )
    assert terminal is not None and terminal.type == "run.failed"


def test_reaper_does_not_override_a_run_that_already_failed_after_cancel(db_session):
    """落终态后 reaper 不得把 failed 覆盖成 cancelled。

    reaper 只选 leased/running；取消标记留在 job 上不得让已经如实落地的失败
    终态被后续清理改写（否则错误分母会凭空消失）。
    """
    user, space = create_agent_fixture(db_session, name="cancel-fail-reap")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None

    agent_queue.request_cancel(db_session, grant.run, actor_id=None)
    db_session.commit()
    agent_queue.settle_run(
        db_session, grant.run, status="failed", error_code="PROVIDER_STREAM_ERROR"
    )

    assert agent_queue.reaper_pass(db_session) == 0
    db_session.expire_all()
    run = db_session.get(AgentRun, grant.run.id)
    assert run is not None and run.status == "failed"
    assert run.error_code == "PROVIDER_STREAM_ERROR"


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


def test_enqueue_invalid_kind_rejected_before_check_constraint(db_session):
    """非法 kind 在服务层先被拒绝，避免绕过并发检查进入队列。"""
    user, space = create_agent_fixture(db_session, name="kindchk")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    with pytest.raises(HTTPException) as exc_info:
        _enqueue(db_session, session, kind="wizard")
    assert _error_code(exc_info.value) == "AGENT_KIND_UNSUPPORTED"
