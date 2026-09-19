"""Durable queue 与 Run/Job FSM（notes.md「统一执行模型裁定」）。

本队列只承载 assistant run；Steward 是独立的确定性引擎，有自己的 StewardJob 表
和 lease（services/steward.py），不经过这里。

全部并发约束在 SQLite 立即事务（BEGIN IMMEDIATE）内执行：
- 每 session 至多一个 active run（partial unique index + 事务内预检）
- 每账户至多 N 个并发 assistant run（事务内预检）

事件落库复用 services/agent_events.py（单向依赖：queue → events）。
调用方须传入干净的 Session（无未提交变更）；本模块自行管理提交边界。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    AGENT_EVENT_INVALID,
    AGENT_JOB_NOT_ACTIVE,
    AGENT_KIND_UNSUPPORTED,
    AGENT_LEASE_EXPIRED,
    AGENT_MEMBERSHIP_REVOKED,
    AGENT_RUN_ACCOUNT_LIMIT,
    AGENT_RUN_NOT_RUNNING,
    AGENT_RUN_SESSION_BUSY,
    AGENT_RUN_TERMINAL,
    IDEMPOTENCY_PAYLOAD_CONFLICT,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import (
    RUN_ACTIVE_STATUSES,
    RUN_TERMINAL_STATUSES,
    RUNTIME_AGENT_KINDS,
    AgentJob,
    AgentMessage,
    AgentRun,
    AgentSession,
)
from app.models.space import SpaceMember
from app.services import agent_events, agent_provider, audit
from app.services.agent_execution import ExecutionIdentity, fence_execution
from app.utils import timeutil

_logger = logging.getLogger(__name__)


@contextmanager
def _immediate_tx(session: Session) -> Iterator[Session]:
    """立即事务：驱动级 BEGIN IMMEDIATE 写锁前置，成功提交，异常整体回滚。"""
    sa_conn = session.connection()
    raw = sa_conn.connection.dbapi_connection
    if not isinstance(raw, sqlite3.Connection):  # pragma: no cover - 仅 SQLite 环境
        raise RuntimeError("agent queue requires a sqlite3 connection")
    if raw.in_transaction:
        raise RuntimeError("agent queue requires a clean session without pending writes")
    sa_conn.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


@dataclass(frozen=True)
class LeaseGrant:
    """一次成功 lease 的配对结果。"""

    job: AgentJob
    run: AgentRun


def enqueue_run(
    db: Session,
    *,
    agent_session: AgentSession,
    kind: str,
    policy_version: str,
    tool_allowlist: list[str],
    message: AgentMessage | None = None,
    max_attempts: int | None = None,
) -> AgentRun:
    """同事务创建 run + job（+ 首个事件）；违反并发约束一律 409 fail-closed。

    interactive Run 携带触发消息时原子写入 message.user_added 事件，
    保证 SSE 重放从用户消息开始不缺序。
    """
    _validate_kind(kind, session_kind=agent_session.agent_kind)
    attempts = max_attempts if max_attempts is not None else config.AGENT_MAX_ATTEMPTS
    with _immediate_tx(db):
        run = _create_run_and_job(
            db,
            agent_session=agent_session,
            kind=kind,
            policy_version=policy_version,
            tool_allowlist=list(tool_allowlist),
            message=message,
            attempts=attempts,
        )
    agent_events.notifier.publish(run.id)
    return run


def _create_run_and_job(
    db: Session,
    *,
    agent_session: AgentSession,
    kind: str,
    policy_version: str,
    tool_allowlist: list[str],
    message: AgentMessage | None,
    attempts: int,
    now: datetime | None = None,
) -> AgentRun:
    """在已持有的立即事务内并发预检并原子写入 run+job(+首个事件)。"""
    _validate_kind(kind, session_kind=agent_session.agent_kind)
    _check_concurrency(db, agent_session=agent_session, kind=kind)
    moment = now or timeutil.utcnow()
    run = AgentRun(
        session_id=agent_session.id,
        message_id=message.id if message is not None else None,
        kind=kind,
        status="queued",
        attempt=0,
        max_attempts=attempts,
        policy_version=policy_version,
        tool_allowlist_json=list(tool_allowlist),
        runtime_snapshot_json=agent_provider.snapshot_for_space(db, agent_session.space_id),
        created_at=moment,
        updated_at=moment,
    )
    db.add(run)
    db.flush()
    job = AgentJob(
        run_id=run.id,
        space_id=agent_session.space_id,
        account_id=agent_session.account_id,
        kind=kind,
        status="queued",
        attempt=0,
        max_attempts=attempts,
        policy_version=policy_version,
        created_at=moment,
        updated_at=moment,
    )
    db.add(job)
    db.flush()
    run.job_id = job.id
    db.flush()
    if message is not None:
        agent_events.insert_event(
            db,
            run,
            seq=0,
            event_type="message.user_added",
            public_payload={
                "message_id": message.id,
                "role": message.role,
                "content": message.content_json,
            },
            created_at=moment,
        )
    return run


def submit_user_message(
    db: Session,
    *,
    agent_session: AgentSession,
    content_json: dict[str, Any],
    idempotency_key: str,
    policy_version: str,
    tool_allowlist: list[str],
) -> tuple[AgentMessage, AgentRun | None, bool]:
    """浏览器消息创建（RT-4 Idempotency）：幂等裁决与 message+run+job 写入同一事务。

    返回 (message, run, replayed)：
    - 同 (session, key) 且 payload 一致 → 幂等返回原 message 与原 run（replayed=True），
      不产生第二次执行；
    - 同 key 但 payload 不同 → 409 IDEMPOTENCY_PAYLOAD_CONFLICT；
    - 新 key → 插入 user 消息并经 _create_run_and_job 建队列（并发超限 409）。

    立即事务串行化保证并发重复提交不会双写：后到者在锁内看到先到者已提交的行，
    走幂等/冲突分支而非唯一索引异常。
    """
    with _immediate_tx(db):
        prior = db.scalar(
            select(AgentMessage).where(
                AgentMessage.session_id == agent_session.id,
                AgentMessage.idempotency_key == idempotency_key,
            )
        )
        if prior is not None:
            if prior.content_json != content_json:
                raise_api_error(
                    409,
                    IDEMPOTENCY_PAYLOAD_CONFLICT,
                    "相同 Idempotency-Key 但请求内容不同",
                    detail={"session_id": agent_session.id},
                )
            run = db.scalar(
                select(AgentRun)
                .where(AgentRun.message_id == prior.id)
                .order_by(AgentRun.id.desc())
                .limit(1)
            )
            return prior, run, True
        now = timeutil.utcnow()
        message = AgentMessage(
            session_id=agent_session.id,
            role="user",
            content_json=dict(content_json),
            idempotency_key=idempotency_key,
            created_at=now,
        )
        db.add(message)
        db.flush()
        run = _create_run_and_job(
            db,
            agent_session=agent_session,
            kind="assistant",
            policy_version=policy_version,
            tool_allowlist=list(tool_allowlist),
            message=message,
            attempts=config.AGENT_MAX_ATTEMPTS,
            now=now,
        )
    agent_events.notifier.publish(run.id)
    return message, run, False


def _check_concurrency(db: Session, *, agent_session: AgentSession, kind: str) -> None:
    """RT-2 并发约束预检（立即事务内，无竞态窗口）。"""
    _validate_kind(kind, session_kind=agent_session.agent_kind)
    active = tuple(RUN_ACTIVE_STATUSES)
    same_session = db.scalar(
        select(sa.func.count(AgentRun.id)).where(
            AgentRun.session_id == agent_session.id, AgentRun.status.in_(active)
        )
    )
    if same_session:
        raise_api_error(409, AGENT_RUN_SESSION_BUSY, "该会话已有执行中的 Run")
    used = db.scalar(
        select(sa.func.count(AgentRun.id))
        .join(AgentSession, AgentSession.id == AgentRun.session_id)
        .where(
            AgentSession.account_id == agent_session.account_id,
            AgentRun.kind == "assistant",
            AgentRun.status.in_(active),
        )
    )
    if used is not None and used >= config.AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT:
        raise_api_error(409, AGENT_RUN_ACCOUNT_LIMIT, "并发 Assistant Run 已达账户上限")


def _validate_kind(
    kind: str | None, *, session_kind: str | None = None, allow_none: bool = False
) -> None:
    """Reject unsupported runtime kinds before any queue read or write."""
    if (
        (kind is None and not allow_none)
        or (kind is not None and kind not in RUNTIME_AGENT_KINDS)
        or (
            session_kind is not None
            and (session_kind not in RUNTIME_AGENT_KINDS or kind is None or session_kind != kind)
        )
    ):
        raise_api_error(
            422,
            AGENT_KIND_UNSUPPORTED,
            "Agent Runtime 只支持 Assistant",
            detail={"kind": kind, "session_kind": session_kind},
        )


def lease_next(
    db: Session,
    *,
    kind: str,
    leased_by: str,
    ttl_seconds: int | None = None,
) -> LeaseGrant | None:
    """租赁最早 queued assistant job；attempt 在每次 lease 时 +1。"""
    _validate_kind(kind)
    ttl = ttl_seconds if ttl_seconds is not None else config.AGENT_LEASE_TTL_SECONDS
    with _immediate_tx(db):
        stmt = select(AgentJob).where(AgentJob.status == "queued", AgentJob.kind == kind)
        job = db.scalar(stmt.order_by(AgentJob.created_at.asc(), AgentJob.id.asc()).limit(1))
        if job is None:
            return None
        now = timeutil.utcnow()
        expires = now + timedelta(seconds=ttl)
        job.status = "leased"
        job.attempt += 1
        job.leased_by = leased_by
        job.lease_expires_at = expires
        job.heartbeat_at = now
        job.updated_at = now
        run = db.get(AgentRun, job.run_id)
        assert run is not None  # FK 保证存在
        run.status = "leased"
        run.attempt = job.attempt
        run.max_attempts = job.max_attempts
        run.lease_expires_at = expires
        run.heartbeat_at = now
        # 首次取得执行权的权威时刻（09-17 D）：只在 attempt 0→1 时写一次。
        # lease_expires_at 被心跳持续前移，不能反推被租走时刻；该列不可变。
        if run.first_leased_at is None:
            run.first_leased_at = now
        run.updated_at = now
        db.flush()
        return LeaseGrant(job=job, run=run)


def heartbeat(
    db: Session,
    job: AgentJob,
    ttl_seconds: int | None = None,
    *,
    execution: ExecutionIdentity | None = None,
) -> datetime:
    """续租：同步更新 job 与 run 的 lease_expires_at / heartbeat_at。"""
    ttl = ttl_seconds if ttl_seconds is not None else config.AGENT_LEASE_TTL_SECONDS
    with _immediate_tx(db):
        if execution is not None:
            _run, _session, job = fence_execution(db, execution, allow_cancel_requested=True)
        else:
            db.refresh(job)
        if job.status not in ("leased", "running"):
            raise_api_error(409, AGENT_JOB_NOT_ACTIVE, "Job 不在活跃状态，无法续租")
        run = db.get(AgentRun, job.run_id)
        assert run is not None
        # Cancellation is a terminal-intent fence.  Once requested, a
        # heartbeat may report the existing expiry but must never extend it;
        # otherwise a healthy sidecar could keep a cancelled run leased
        # forever and prevent reaper convergence.
        if job.cancel_requested or run.cancel_requested:
            return job.lease_expires_at or timeutil.utcnow()
        now = timeutil.utcnow()
        expires = now + timedelta(seconds=ttl)
        job.lease_expires_at = expires
        job.heartbeat_at = now
        job.updated_at = now
        run.lease_expires_at = expires
        run.heartbeat_at = now
        run.updated_at = now
        db.flush()
        return expires


def settle_run(
    db: Session,
    run: AgentRun,
    *,
    status: str,
    error_code: str | None = None,
    error: dict[str, object] | None = None,
    execution: ExecutionIdentity | None = None,
) -> AgentRun:
    """终态落库（sidecar 结算路径）：succeeded|failed 仅可从 leased/running 进入。

    cancel_requested 改判（RT-6 恢复语义）：sidecar 结算 succeeded 但浏览器已
    请求取消 → 本应 succeeded 的终态改判为 cancelled（结果丢弃，审计注明）；
    failed 原样保留。改判判定在锁内复核，避免读后竞态。
    """
    if status not in ("succeeded", "failed"):
        raise_api_error(422, AGENT_EVENT_INVALID, "非法的终态", detail={"status": status})
    if run.status in RUN_TERMINAL_STATUSES:
        raise_api_error(409, AGENT_RUN_TERMINAL, "Run 已是终态", detail={"status": run.status})
    if run.status not in ("leased", "running"):
        raise_api_error(
            409,
            AGENT_RUN_NOT_RUNNING,
            "仅 leased/running 可进入终态",
            detail={"status": run.status},
        )
    settled = _settle(
        db, run, status=status, error_code=error_code, error=error, execution=execution
    )
    agent_events.notifier.publish(run.id)
    return settled


def _settle(
    db: Session,
    run: AgentRun,
    *,
    status: str,
    error_code: str | None,
    error: dict[str, object] | None,
    execution: ExecutionIdentity | None = None,
) -> AgentRun:
    """终态写入 + 对应终态事件追加（同一立即事务；终态不可复活）。"""
    with _immediate_tx(db):
        if execution is not None:
            run, _session, _job = fence_execution(db, execution, allow_cancel_requested=True)
        else:
            db.refresh(run)
        if run.status in RUN_TERMINAL_STATUSES:
            raise_api_error(409, AGENT_RUN_TERMINAL, "Run 已是终态")
        effective = status
        if effective == "succeeded" and run.cancel_requested:
            effective = "cancelled"
            audit.write_audit(
                db,
                action="agent_run_settle_overridden",
                actor_id=None,
                target_id=run.id,
                detail={"requested": status, "final": "cancelled", "reason": "cancel_requested"},
            )
        now = timeutil.utcnow()
        run.status = effective
        run.settled_at = now
        run.updated_at = now
        run.error_code = error_code
        run.error_json = error
        job = db.get(AgentJob, run.job_id) if run.job_id is not None else None
        if job is not None:
            job.status = effective
            job.updated_at = now
            job.error_json = error
        agent_events.insert_event(
            db,
            run,
            seq=agent_events.next_seq(db, run.id),
            event_type=agent_events.TERMINAL_EVENT_FOR[effective],
            public_payload={
                "status": effective,
                **({"error_code": error_code} if error_code else {}),
            },
            created_at=now,
        )
        if effective == "succeeded" and run.message_id is not None:
            # 2026-09-15 审核修复：成功结算后对本次 user 消息做确定性记忆候选
            # 提取（只提 review card，确认仍由用户完成）。提取自身全部容错，
            # 绝不影响终态落库；与终态同事务提交。
            try:
                from app.services import memory_extractor

                memory_extractor.extract_after_settle(db, run)
            except Exception:
                _logger.warning(
                    "memory extraction hook failed run=%s error swallowed",
                    run.id,
                    exc_info=True,
                )
        db.flush()
        return run


def cancel_run(db: Session, run: AgentRun) -> AgentRun:
    """取消：允许从 queued/leased/running 进入 cancelled（终态拒绝）。"""
    if run.status in RUN_TERMINAL_STATUSES:
        raise_api_error(
            409, AGENT_RUN_TERMINAL, "Run 已是终态，不可取消", detail={"status": run.status}
        )
    # cancelled 允许从 queued 直接入终态（尚未 lease 的排队任务可直接撤）
    return _settle(db, run, status="cancelled", error_code=None, error=None)


def request_cancel(db: Session, run: AgentRun, *, actor_id: int | None = None) -> AgentRun:
    """浏览器取消入口（RT-6 恢复语义；api 层负责归属审计的 actor 归属）。

    - queued：立即进入 cancelled 终态（含 run.cancelled 事件，未 lease 的任务直接撤）；
    - leased/running：置 cancel_requested 标记（run/job 镜像），settle 时把本应
      succeeded 的终态改判为 cancelled（结果丢弃、审计注明）；failed 原样保留；
      若 lease 过期则由 reaper 直接按 cancelled 收敛，不回队重试；
    - 终态一律 409（锁内复核，防读后竞态）。
    """
    if run.status in RUN_TERMINAL_STATUSES:
        raise_api_error(
            409, AGENT_RUN_TERMINAL, "Run 已是终态，不可取消", detail={"status": run.status}
        )
    if run.status == "queued":
        settled = _settle(db, run, status="cancelled", error_code=None, error=None)
        audit.write_audit(
            db,
            action="agent_run_cancelled",
            actor_id=actor_id,
            target_id=run.id,
            detail={"path": "queued_immediate"},
        )
        db.commit()
        agent_events.notifier.publish(settled.id)
        return settled
    with _immediate_tx(db):
        db.expire(run, ("status",))
        if run.status in RUN_TERMINAL_STATUSES:
            raise_api_error(
                409,
                AGENT_RUN_TERMINAL,
                "Run 已是终态，不可取消",
                detail={"status": run.status},
            )
        now = timeutil.utcnow()
        run.cancel_requested = True
        run.updated_at = now
        job = db.get(AgentJob, run.job_id) if run.job_id is not None else None
        if job is not None:
            job.cancel_requested = True
            job.updated_at = now
        audit.write_audit(
            db,
            action="agent_run_cancel_requested",
            actor_id=actor_id,
            target_id=run.id,
            detail={"status": run.status},
        )
        db.flush()
    return run


def reaper_pass(db: Session, *, now: datetime | None = None) -> int:
    """回收过期 lease：attempt 未耗尽回队（可重试），耗尽判 expired 终态。

    返回处理的 job 数；每次处理写安全审计（可观测崩溃/超时恢复语义）。

    ``cancel_requested`` 是服务端权威的取消意图，不等 lease 自然过期：
    在途 sidecar 看到取消裁决后就不再写入也不结算（F 的 A6 语义），所以
    只靠「等 lease 过期」会让浏览器把取消后的等待一直转到租约结束
    （实测 304s，而 ``AGENT_LEASE_TTL_SECONDS`` 默认 300）。取消是终态意图，
    与租约是否过期无关，因此这里直接按终态收敛。

    ``membership revoked`` 同理（09-19 受控验收发现）：执行身份被永久撤销后，
    每次内部请求（含心跳）都被 ``_authorize_run`` 拒（403
    ``active_membership_missing``），sidecar 不再续租也不再结算，于是没有任何
    一方写终态。旧逻辑把它当作可重试的租约丢失回队，新 attempt 立刻又 403，
    直到 ``attempt`` 耗尽（约 3×300s）才收口为 ``expired``。用户在这 15 分钟里
    看着一个永远不会完成的「生成中…」，而 ``expired`` 还把「授权永久失效」
    记成「租约超时」。重试不可能改变结果，因此与取消一样直接终态化。
    """
    moment = now or timeutil.utcnow()

    with _immediate_tx(db):
        # 两组合并：① 租约过期或已请求取消；② 执行身份的成员资格已永久撤销
        # （即使租约仍然健康）。第二组让撤权在下一个维护 tick 内收敛，而不是等
        # 满一个租约周期——否则用户会看着「生成中…」直到 300s 后才收口。
        stale = list(
            db.scalars(
                select(AgentJob).where(
                    AgentJob.status.in_(("leased", "running")),
                    or_(
                        AgentJob.cancel_requested.is_(True),
                        and_(
                            AgentJob.lease_expires_at.is_not(None),
                            AgentJob.lease_expires_at < moment,
                        ),
                        AgentJob.id.in_(_revoked_membership_job_ids(db)),
                    ),
                )
            )
        )
        for job in stale:
            exhausted = job.attempt >= job.max_attempts
            if job.cancel_requested:
                # 已请求取消的执行不再回队重试：直接按取消终态收敛（结果丢弃）
                outcome = "cancelled"
                error_code: str | None = None
                reason = "cancel_requested"
            elif _execution_identity_revoked(db, job):
                # 永久失效先于 attempt 判定：否则一个 attempt 已耗尽的撤权 Run
                # 会被记成 expired（租约超时），继续混淆失败分母。
                outcome = "failed"
                error_code = AGENT_MEMBERSHIP_REVOKED
                reason = "membership_revoked"
            else:
                outcome = "expired" if exhausted else "queued"
                error_code = AGENT_LEASE_EXPIRED if exhausted else None
                reason = "lease_expired"
            job.status = outcome
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.updated_at = moment
            run = db.get(AgentRun, job.run_id)
            assert run is not None
            run.status = outcome
            run.lease_expires_at = None
            run.heartbeat_at = None
            run.updated_at = moment
            if outcome in ("expired", "cancelled", "failed"):
                run.settled_at = moment
                # 终态事件由服务端唯一写入（不含 sidecar）：reaper 与 settle/cancel 同口径
                agent_events.insert_event(
                    db,
                    run,
                    seq=agent_events.next_seq(db, run.id),
                    event_type=agent_events.TERMINAL_EVENT_FOR[outcome],
                    public_payload={
                        "status": outcome,
                        **({"error_code": error_code} if error_code else {}),
                    },
                    created_at=moment,
                )
            if error_code is not None:
                run.error_code = error_code
            audit.write_audit(
                db,
                action="agent_lease_expired",
                actor_id=None,
                target_id=job.run_id,
                detail={
                    "job_id": job.id,
                    "attempt": job.attempt,
                    "outcome": outcome,
                    "reason": reason,
                },
            )
        return len(stale)


def _revoked_membership_job_ids(db: Session) -> sa.Select[tuple[int]]:
    """活跃 job 中「执行身份成员资格已失效」的 id 集合（SQL 侧过滤，不拉全表）。

    与 ``fence_execution`` / ``_authorize_run`` 使用**同一判据**
    （job.space_id + 账号对应 user + status='active'），不另立一套授权规则。
    账号/会话缺失也归为失效：身份已不可解析，重试不可能成功。
    """
    member = sa.orm.aliased(SpaceMember)
    active_member = (
        select(member.id)
        .where(
            member.space_id == AgentJob.space_id,
            member.user_id == Account.user_id,
            member.status == "active",
        )
        .exists()
    )
    return (
        select(AgentJob.id)
        .join(AgentRun, AgentRun.job_id == AgentJob.id)
        .outerjoin(AgentSession, AgentSession.id == AgentRun.session_id)
        .outerjoin(Account, Account.id == AgentSession.account_id)
        .where(AgentJob.status.in_(("leased", "running")), ~active_member)
    )


def _execution_identity_revoked(db: Session, job: AgentJob) -> bool:
    """执行身份是否已永久失效（成员资格不再是 active）。

    与 ``fence_execution`` / ``_authorize_run`` 使用**同一判据**（空间 + 账号对应
    用户 + status='active'），不另立一套授权规则。

    只在该查询**成功且返回 None** 时判为失效；查询本身失败会向上抛异常，让整个
    tick 回滚并在下一 tick 重试——不得把「读不到」当作「已失效」而终态化健康 Run。

    账号/会话缺失也归为失效（与 SQL 侧 ``_revoked_membership_job_ids`` 一致）：
    执行身份已不可解析，重试同样不可能成功。
    """
    run = db.get(AgentRun, job.run_id)
    if run is None:
        return True
    agent_session = db.get(AgentSession, run.session_id)
    if agent_session is None:
        return True
    account = db.get(Account, agent_session.account_id)
    if account is None:
        return True
    member = db.scalar(
        select(SpaceMember.id).where(
            SpaceMember.space_id == job.space_id,
            SpaceMember.user_id == account.user_id,
            SpaceMember.status == "active",
        )
    )
    return member is None


def prune_finished(db: Session, *, older_than: datetime) -> int:
    """后台清理入口：删除 settled_at 早于阈值的终态 run（events/job 由 CASCADE 清除）。"""
    with _immediate_tx(db):
        result = db.execute(
            sa.delete(AgentRun).where(
                AgentRun.status.in_(RUN_TERMINAL_STATUSES),
                AgentRun.settled_at.is_not(None),
                AgentRun.settled_at < older_than,
            )
        )
        return int(result.rowcount or 0)
