"""Immutable signed execution identity, checked inside short SQLite writers.

Request authentication is an early rejection only. Every admission/writer must
carry the original identity here, before reading seq/build or changing state.
The caller owns commit/rollback; never hold this writer over network I/O.

Two identities exist because the two runtime kinds have genuinely different
scopes:

- :class:`ExecutionIdentity` (assistant) is anchored on an **account**: a run
  belongs to ``(account, space, session, queue job)`` and authorization is the
  account's active membership.
- :class:`StewardExecution` is anchored on a **space**: a Steward child run
  belongs to ``(space, StewardJob, assist batch)`` and has no single account
  (only terminology binds a viewer). It carries no ``account_id`` at all, so a
  mistaken account-scoped check cannot be written against it by accident.

The two fence functions below therefore have **separate, complete** check sets
rather than one function branching on ``kind``. A shared function with a kind
branch is a security hazard: forgetting one check in one branch is a silent
backdoor, and a single regression matrix cannot prove both branches. Each has
its own matrix in ``tests/test_agent_execution_fence.py``.
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
from app.models.steward import StewardJob, StewardModelCall
from app.utils.timeutil import utcnow


@dataclass(frozen=True)
class ExecutionIdentity:
    """Assistant run identity: account-anchored."""

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


@dataclass(frozen=True)
class StewardExecution:
    """Steward child run identity: space-anchored, deliberately without account_id."""

    run_id: int
    steward_job_id: int
    expected_attempt: int
    space_id: int
    viewer_account_id: int | None
    agent_kind: str
    tool_allowlist: tuple[str, ...]

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> StewardExecution:
        return cls(
            run_id=claims["run_id"],
            steward_job_id=claims["job_id"],
            expected_attempt=claims["attempt"],
            space_id=claims["space_id"],
            viewer_account_id=claims.get("viewer_account_id"),
            agent_kind=claims["agent_kind"],
            tool_allowlist=tuple(sorted(claims["tool_allowlist"])),
        )


Execution = ExecutionIdentity | StewardExecution


def execution_from_claims(claims: dict[str, Any]) -> Execution:
    """Build the identity matching the token's kind.

    ``agent_kind`` decides which identity is constructed, so a steward token can
    never be turned into an account-anchored identity (and vice versa).
    """
    if claims["agent_kind"] == "assistant":
        return ExecutionIdentity.from_claims(claims)
    if claims["agent_kind"] == "steward":
        return StewardExecution.from_claims(claims)
    raise_api_error(403, AGENT_TOKEN_SCOPE_MISMATCH, "未知的执行身份")


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


def _lease_expired(run: AgentRun, *expiries: Any) -> bool:
    """Shared lease arithmetic (not a shared authorization decision)."""
    now = utcnow()
    if run.lease_expires_at is None or run.lease_expires_at <= now:
        return True
    return any(expiry is None or expiry <= now for expiry in expiries)


def _reject_cancel_requested(run: AgentRun, *, job_cancel: bool) -> None:
    if run.cancel_requested or job_cancel:
        # 机器可读的取消标记：调用方（sidecar）必须按「服务端已裁决取消」收敛，
        # 而不是把它当作普通 409 冲突自造 failed 终态。
        raise_api_error(
            409,
            AGENT_RUN_NOT_RUNNING,
            "Run 已请求取消",
            detail={"reason": "cancel_requested"},
        )


def fence_assistant_execution(
    db: Session,
    identity: ExecutionIdentity,
    *,
    allowed_statuses: tuple[str, ...] = ("leased", "running"),
    allow_cancel_requested: bool = False,
) -> tuple[AgentRun, AgentSession, AgentJob]:
    """Admit an assistant run. Every check below is assistant-specific."""
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
    if not allow_cancel_requested:
        _reject_cancel_requested(run, job_cancel=job.cancel_requested)
    if _lease_expired(run, job.lease_expires_at):
        raise_api_error(409, AGENT_LEASE_EXPIRED, "执行租约已过期")
    return run, session, job


def fence_steward_execution(
    db: Session,
    identity: StewardExecution,
    *,
    allowed_statuses: tuple[str, ...] = ("leased", "running"),
    allow_cancel_requested: bool = False,
) -> tuple[AgentRun, StewardModelCall, StewardJob]:
    """Admit a Steward child run.

    Authorization root is the **parent StewardJob**, not an account: the run is
    space-scoped and has no single account. The layered judgment (accepted
    2026-09-25, see design §5.2.1) is:

    1. the parent job is the job this attempt was reserved for and it completed
       (``succeeded``) — this is the grant, and reservation in turn required the
       space to have Steward enabled with an active member;
    2. the space still exists;
    3. when ``viewer_account_id`` is set (terminology only), that account's user
       must still be an active member — the same judgment as assistant.

    Residual risk (recorded, not hidden): layer 2 does not by itself stop a
    revoked user's data from being processed at space scope, and convergence
    after revocation is bounded by one maintenance tick rather than the next
    internal request. The regression suite must prove revocation converges
    within a tick, and that removing any single layer fails a test.
    """
    acquire_run_writer(db, identity.run_id)
    run = db.get(AgentRun, identity.run_id, populate_existing=True)
    # The attempt IS the scope: job, kind and viewer are its own columns, and its
    # run_id is UNIQUE, so this single indexed read replaces the old steward_runs
    # lookup without losing any invariant.
    attempt = (
        db.scalar(
            select(StewardModelCall)
            .where(StewardModelCall.run_id == identity.run_id)
            .execution_options(populate_existing=True)
        )
        if run is not None
        else None
    )
    job = db.get(StewardJob, identity.steward_job_id, populate_existing=True)

    # Structural linkage. A steward run must be space-scoped: no session and no
    # queue job (``agent_runs.job_id`` stays NULL; the parent is the attempt's
    # ``job_id``).
    #
    # ``attempt.job_id != job.id`` covers the token's ``steward_job_id`` too:
    # ``job`` was fetched *by* that claim, so comparing the attempt against both
    # would be the same comparison written twice (verified by mutation — removing
    # either one alone leaves the other rejecting the forged-job case).
    if (
        run is None
        or attempt is None
        or job is None
        or run.session_id is not None
        or run.job_id is not None
        or run.kind != identity.agent_kind
        or attempt.job_id != job.id
        or run.attempt != identity.expected_attempt
        or tuple(sorted(run.tool_allowlist_json or [])) != identity.tool_allowlist
        or job.space_id != identity.space_id
        # The attempt denormalises the space (it is created from the plan, which
        # belongs to the job), so this is defence in depth against that invariant
        # breaking rather than an independent gate — a forged space_id trips the
        # job comparison above first (verified by mutation).
        or attempt.space_id != identity.space_id
        or attempt.viewer_account_id != identity.viewer_account_id
    ):
        raise_api_error(403, AGENT_TOKEN_SCOPE_MISMATCH, "执行身份或成员资格已变化")

    # Layer 3: a named viewer must still be an active member of this space.
    if identity.viewer_account_id is not None:
        viewer = db.get(Account, identity.viewer_account_id, populate_existing=True)
        if (
            viewer is None
            or db.scalar(
                select(SpaceMember.id).where(
                    SpaceMember.space_id == identity.space_id,
                    SpaceMember.user_id == viewer.user_id,
                    SpaceMember.status == "active",
                )
            )
            is None
        ):
            raise_api_error(403, AGENT_TOKEN_SCOPE_MISMATCH, "执行身份或成员资格已变化")

    # Layer 2: the space still exists.
    #
    # Not independently isolatable through a forged token: forging ``space_id``
    # trips the equality comparisons above first, and deleting the space cascades
    # the attempt away (so there is no attempt left to fence). It is kept because
    # it covers the window between "attempt reserved" and "space deleted" for a
    # *valid* token, which no forged-token case can reach.
    from app.models.space import FamilySpace

    if db.get(FamilySpace, identity.space_id) is None:
        raise_api_error(403, AGENT_TOKEN_SCOPE_MISMATCH, "执行身份或成员资格已变化")

    # Layer 1 (continued): the parent job must still be the completed job this
    # batch was registered against.
    #
    # ``succeeded`` — not ``leased``/``running`` — because an assist batch is only
    # registered *after* the deterministic core completes (see
    # ``steward_delivery``'s assist intent → ``register_batch_for_job``), and
    # ``_fence_check`` already gates lease time on the same condition. Requiring an
    # active job here would reject every child run at its own fence. (The design's
    # §5.2.1 wording said "status IN ('leased','running')"; that is inconsistent
    # with the registration flow and was corrected in implementation.)
    if job.status != "succeeded":
        raise_api_error(409, AGENT_RUN_NOT_RUNNING, "父 Steward 作业不在可执行状态")

    if run.status not in allowed_statuses:
        raise_api_error(409, AGENT_RUN_NOT_RUNNING, "Run 不在可执行状态")
    if not allow_cancel_requested:
        _reject_cancel_requested(run, job_cancel=False)

    # Lease: the run lease and the attempt lease must both be live. They are
    # renewed together by heartbeat_child_run, so a mismatch here means one side
    # was lost and the attempt must not continue.
    expiries: list[Any] = [attempt.lease_until]
    if attempt.status != "in_flight":
        raise_api_error(409, AGENT_RUN_NOT_RUNNING, "辅助 attempt 不在执行状态")
    if _lease_expired(run, *expiries):
        raise_api_error(409, AGENT_LEASE_EXPIRED, "执行租约已过期")
    return run, attempt, job


def fence_execution(
    db: Session,
    identity: Execution,
    *,
    allowed_statuses: tuple[str, ...] = ("leased", "running"),
    allow_cancel_requested: bool = False,
) -> tuple[AgentRun, AgentSession | StewardModelCall, AgentJob | StewardJob]:
    """Dispatch to the fence matching the identity's type.

    Dispatch is on the *type*, never on a string field: a caller holding an
    :class:`ExecutionIdentity` cannot reach the steward path, and vice versa.
    The two implementations keep independent, complete check sets; this
    function only routes.
    """
    if isinstance(identity, StewardExecution):
        return fence_steward_execution(
            db,
            identity,
            allowed_statuses=allowed_statuses,
            allow_cancel_requested=allow_cancel_requested,
        )
    if isinstance(identity, ExecutionIdentity):
        return fence_assistant_execution(
            db,
            identity,
            allowed_statuses=allowed_statuses,
            allow_cancel_requested=allow_cancel_requested,
        )
    raise_api_error(403, AGENT_TOKEN_SCOPE_MISMATCH, "未知的执行身份")
