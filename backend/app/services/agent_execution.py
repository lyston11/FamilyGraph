"""Immutable signed execution identity, checked inside short SQLite writers.

Request authentication is an early rejection only. Every admission/writer must
carry the original identity here, before reading seq/build or changing state.
The caller owns commit/rollback; never hold this writer over network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.errors import (
    AGENT_LEASE_EXPIRED,
    AGENT_RUN_NOT_RUNNING,
    AGENT_TOKEN_SCOPE_MISMATCH,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import AgentJob, AgentRun, AgentSession
from app.models.space import SpaceMember
from app.utils.timeutil import utcnow


@dataclass(frozen=True)
class ExecutionIdentity:
    run_id: int
    job_id: int
    expected_attempt: int
    account_id: int
    space_id: int
    agent_kind: str
    tool_allowlist: tuple[str, ...]

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> ExecutionIdentity:
        return cls(
            run_id=claims["run_id"],
            job_id=claims["job_id"],
            expected_attempt=claims["attempt"],
            account_id=claims["account_id"],
            space_id=claims["space_id"],
            agent_kind=claims["agent_kind"],
            tool_allowlist=tuple(sorted(claims["tool_allowlist"])),
        )


def acquire_run_writer(db: Session, run_id: int) -> None:
    """Serialize with reaper/lease/cancel before any mutable gate read.

    No-op UPDATE also works inside an existing short transaction. Production
    callers have no pending mutations at this point; trusted internal callers
    may already hold the writer (for example a run.started promotion).
    """
    db.execute(
        text("UPDATE agent_runs SET updated_at = updated_at WHERE id = :run_id"),
        {"run_id": run_id},
    )


def fence_execution(
    db: Session,
    identity: ExecutionIdentity,
    *,
    allowed_statuses: tuple[str, ...] = ("leased", "running"),
    allow_cancel_requested: bool = False,
) -> tuple[AgentRun, AgentSession, AgentJob]:
    acquire_run_writer(db, identity.run_id)
    run = db.get(AgentRun, identity.run_id, populate_existing=True)
    job = db.get(AgentJob, identity.job_id, populate_existing=True)
    session = (
        db.get(AgentSession, run.session_id, populate_existing=True) if run is not None else None
    )
    account = db.get(Account, identity.account_id, populate_existing=True)
    if (
        run is None
        or job is None
        or session is None
        or account is None
        or run.job_id != identity.job_id
        or job.run_id != identity.run_id
        or run.attempt != identity.expected_attempt
        or job.attempt != identity.expected_attempt
        or session.account_id != identity.account_id
        or job.account_id != identity.account_id
        or session.space_id != identity.space_id
        or job.space_id != identity.space_id
        or run.kind != identity.agent_kind
        or job.kind != identity.agent_kind
        or session.agent_kind != identity.agent_kind
        or tuple(sorted(run.tool_allowlist_json or [])) != identity.tool_allowlist
        or db.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == identity.space_id,
                SpaceMember.user_id == account.user_id,
                SpaceMember.status == "active",
            )
        )
        is None
    ):
        raise_api_error(403, AGENT_TOKEN_SCOPE_MISMATCH, "执行身份或成员资格已变化")
    if run.status not in allowed_statuses or job.status not in allowed_statuses:
        raise_api_error(409, AGENT_RUN_NOT_RUNNING, "Run/Job 不在可执行状态")
    if not allow_cancel_requested and (run.cancel_requested or job.cancel_requested):
        raise_api_error(409, AGENT_RUN_NOT_RUNNING, "Run 已请求取消")
    now = utcnow()
    if (
        run.lease_expires_at is None
        or job.lease_expires_at is None
        or run.lease_expires_at <= now
        or job.lease_expires_at <= now
    ):
        raise_api_error(409, AGENT_LEASE_EXPIRED, "执行租约已过期")
    return run, session, job
