"""Security matrix for the two execution fences.

Why two fences instead of one kind-branching function: a branch is a hazard —
a check missing from one arm is a silent backdoor, and a single matrix cannot
prove both arms. These tests therefore cover **each fence independently** and
assert the cross-kind isolation property directly.

The steward matrix is written so that *every* layer has a case that fails when
that layer is removed (design §5.2.1 requires this: "移除任一检查则回归失败").
Each case mutates exactly one fact and asserts the fence rejects, so a deleted
check turns exactly one case green-by-accident into a visible failure.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.errors import (
    AGENT_LEASE_EXPIRED,
    AGENT_RUN_NOT_RUNNING,
    AGENT_TOKEN_SCOPE_MISMATCH,
    extract_api_error,
)
from app.models.account import Account
from app.models.agent import AgentRun, AgentSession
from app.models.space import SpaceMember
from app.models.steward import StewardAssistBatch, StewardJob, StewardRun
from app.services.agent_execution import (
    ExecutionIdentity,
    StewardExecution,
    execution_from_claims,
    fence_assistant_execution,
    fence_execution,
    fence_steward_execution,
)
from app.services.agent_queue import enqueue_run, lease_next
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture, create_agent_session


def _error(exc_info):
    """Return the structured api-error payload from a raised HTTPException."""
    payload = extract_api_error(getattr(exc_info.value, "detail", None))
    assert payload is not None, f"not a structured api error: {exc_info.value!r}"
    return payload


def _assistant_world(db: Session, *, name: str = "fence-assistant-matrix"):
    """Lease a real assistant run and return (user, space, run, job_id, identity)."""
    user, space = create_agent_fixture(db, name=name)
    session_row = create_agent_session(db, account_id=user.account.id, space_id=space.id)
    run = enqueue_run(
        db, agent_session=session_row, kind="assistant", policy_version="p1", tool_allowlist=[]
    )
    grant = lease_next(db, kind="assistant", leased_by="test-sidecar")
    assert grant is not None and grant.run.id == run.id
    run = db.get(AgentRun, run.id)
    identity = ExecutionIdentity(
        run_id=run.id,
        job_id=run.job_id,
        expected_attempt=run.attempt,
        account_id=user.account.id,
        space_id=space.id,
        agent_kind="assistant",
        tool_allowlist=(),
    )
    return user, space, run, identity


def _assert_scope_mismatch(db, identity):
    with pytest.raises(Exception) as exc:
        fence_assistant_execution(db, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def _steward_world(db: Session, *, with_batch: bool = True, assist_kind: str = "terminology"):
    """Build the minimal valid steward execution world and return its ids."""
    user, space = create_agent_fixture(db, name="fence-steward")
    now = utcnow()

    # succeeded, not running: assist batches are registered only after the
    # deterministic core completes, so that is the state a child run is admitted
    # against.
    job = StewardJob(
        space_id=space.id,
        cause="integrity_scan",
        trigger_cursor=1,
        status="succeeded",
        attempt=1,
        max_attempts=3,
        checkpoint_json={},
        policy_version="p1",
        lease_expires_at=now + timedelta(seconds=300),
        leased_by="test-worker",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()

    batch = None
    if with_batch:
        batch = StewardAssistBatch(
            space_id=space.id,
            job_id=job.id,
            evidence_hash="e" * 64,
            policy_version="p1",
            status="leased",
            attempt=1,
            lease_owner="test-worker",
            lease_until=now + timedelta(seconds=300),
            fence_json={},
            created_at=now,
            updated_at=now,
        )
        db.add(batch)
        db.flush()

    # A steward run: no session, no queue job (DB-enforced by scope binding).
    run = AgentRun(
        session_id=None,
        message_id=None,
        job_id=None,
        kind="steward",
        status="running",
        attempt=1,
        max_attempts=1,
        policy_version="p1",
        tool_allowlist_json=[],
        lease_expires_at=now + timedelta(seconds=300),
        heartbeat_at=now,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.flush()

    steward_run = StewardRun(
        run_id=run.id,
        steward_job_id=job.id,
        assist_batch_id=batch.id if batch is not None else None,
        assist_kind=assist_kind,
        # terminology binds a viewer (DB CHECK enforces the biconditional).
        viewer_account_id=user.account.id if assist_kind == "terminology" else None,
        fence_json={},
        created_at=now,
    )
    db.add(steward_run)
    db.commit()
    return user, space, job, batch, run, steward_run


def _identity(run, job, batch, *, space_id, assist_kind="terminology", viewer_account_id=None):
    return StewardExecution(
        run_id=run.id,
        steward_job_id=job.id,
        expected_attempt=1,
        space_id=space_id,
        steward_batch_id=batch.id if batch is not None else None,
        viewer_account_id=viewer_account_id,
        agent_kind="steward",
        tool_allowlist=(),
    )


# --------------------------------------------------------------------------
# Steward fence: the positive path
# --------------------------------------------------------------------------


def test_steward_fence_admits_a_fully_bound_run(db_session):
    user, space, job, batch, run, steward_run = _steward_world(db_session)
    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)

    admitted_run, admitted_steward, admitted_job = fence_steward_execution(db_session, identity)

    assert admitted_run.id == run.id
    assert admitted_steward.steward_job_id == job.id
    assert admitted_job.id == job.id


def test_steward_fence_works_without_a_batch(db_session):
    """The batch is optional (it may be superseded); the job is the scope source."""
    _, space, job, _batch, run, _ = _steward_world(
        db_session, with_batch=False, assist_kind="candidate"
    )
    identity = _identity(run, job, None, space_id=space.id, viewer_account_id=None)

    admitted_run, _, _ = fence_steward_execution(db_session, identity)
    assert admitted_run.id == run.id


def test_steward_fence_accepts_a_non_terminology_kind_without_viewer(db_session):
    _, space, job, batch, run, _ = _steward_world(db_session, assist_kind="candidate")
    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=None)

    assert fence_steward_execution(db_session, identity)[0].id == run.id


# --------------------------------------------------------------------------
# Steward fence: one case per layer, each fatal on its own
# --------------------------------------------------------------------------


def test_steward_fence_rejects_wrong_space(db_session):
    """Layer 2: the run's space comes from the parent job and must match.

    Two properties are needed for this case to actually isolate the comparison
    (both verified by mutation): the kind must have **no** viewer, otherwise the
    viewer-membership query fails on the wrong space too; and the forged space
    must **exist**, otherwise the space-existence check fires instead.
    """
    _, space, job, batch, run, _ = _steward_world(db_session, assist_kind="candidate")
    _, other_space = create_agent_fixture(db_session, name="fence-other-space")
    identity = _identity(run, job, batch, space_id=other_space.id, viewer_account_id=None)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_fence_rejects_revoked_viewer(db_session):
    """Layer 3: a bound viewer must still be an active member."""
    user, space, job, batch, run, _ = _steward_world(db_session)
    member = db_session.query(SpaceMember).filter_by(space_id=space.id, user_id=user.id).one()
    member.status = "removed"
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_fence_rejects_parent_job_not_settled(db_session):
    """Layer 1: the parent job is the authorization root and must have completed.

    A batch is only registered after the deterministic core succeeds, so the
    executable parent state is ``succeeded`` — not ``leased``/``running``. A job
    still in flight (or failed) must not authorize a child run.
    """
    user, space, job, batch, run, _ = _steward_world(db_session)
    job.status = "running"
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 409


def test_steward_fence_rejects_attempt_mismatch(db_session):
    user, space, job, batch, run, _ = _steward_world(db_session)
    run.attempt = 2
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_fence_rejects_allowlist_drift(db_session):
    user, space, job, batch, run, _ = _steward_world(db_session)
    run.tool_allowlist_json = ["familygraph.echo"]
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_fence_rejects_viewer_mismatch(db_session):
    """A viewer claim must match the bound viewer exactly (no smuggling).

    The forged viewer is made an **active member** of the same space, so the
    membership layer cannot reject it — only the claim-equality check can.
    Otherwise this case would pass with that check deleted (verified by
    mutation), and would be proving the wrong thing.
    """
    _, space, job, batch, run, steward_run = _steward_world(db_session)
    other_user, _ = create_agent_fixture(db_session, name="fence-other-viewer")
    now = utcnow()
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=other_user.id,
            added_by=other_user.id,
            role="member",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    assert steward_run.viewer_account_id != other_user.account.id

    identity = _identity(
        run, job, batch, space_id=space.id, viewer_account_id=other_user.account.id
    )
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_fence_rejects_forged_job_id(db_session):
    """A token naming a StewardJob that does not exist must not be admitted."""
    user, space, job, batch, run, _ = _steward_world(db_session)
    forged = StewardExecution(
        run_id=run.id,
        steward_job_id=job.id + 9999,
        expected_attempt=1,
        space_id=space.id,
        steward_batch_id=batch.id,
        viewer_account_id=user.account.id,
        agent_kind="steward",
        tool_allowlist=(),
    )
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, forged)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_fence_rejects_batch_mismatch(db_session):
    user, space, job, batch, run, _ = _steward_world(db_session)
    identity = StewardExecution(
        run_id=run.id,
        steward_job_id=job.id,
        expected_attempt=1,
        space_id=space.id,
        steward_batch_id=batch.id + 999,
        viewer_account_id=user.account.id,
        agent_kind="steward",
        tool_allowlist=(),
    )
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_fence_rejects_cancelled_run(db_session):
    user, space, job, batch, run, _ = _steward_world(db_session)
    run.cancel_requested = True
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 409
    # Machine-readable so the sidecar converges instead of inventing a failure.
    assert _error(exc)["detail"]["reason"] == "cancel_requested"


def test_steward_fence_rejects_run_not_in_an_allowed_status(db_session):
    """The child run itself must be in an executable status."""
    user, space, job, batch, run, _ = _steward_world(db_session)
    run.status = "queued"
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 409
    assert _error(exc)["code"] == AGENT_RUN_NOT_RUNNING


def test_steward_fence_rejects_expired_run_lease(db_session):
    user, space, job, batch, run, _ = _steward_world(db_session)
    run.lease_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 409
    assert _error(exc)["code"] == AGENT_LEASE_EXPIRED


def test_steward_fence_rejects_expired_batch_lease(db_session):
    """The batch lease is a second, independent expiry gate."""
    user, space, job, batch, run, _ = _steward_world(db_session)
    batch.lease_until = utcnow() - timedelta(seconds=1)
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 409


def test_steward_fence_rejects_superseded_batch(db_session):
    user, space, job, batch, run, _ = _steward_world(db_session)
    batch.status = "superseded"
    db_session.commit()

    identity = _identity(run, job, batch, space_id=space.id, viewer_account_id=user.account.id)
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------
# Steward fence checks that are *defense in depth*, not independently testable
# --------------------------------------------------------------------------
# Mutation testing (deleting each check in turn) showed the following steward
# comparisons cannot be falsified through the database while the 0055
# constraints exist. They are kept deliberately: the fence must not depend on a
# constraint being present in every environment or on a future migration not
# relaxing one. Each is listed with the reason it cannot be isolated, so a
# reader does not mistake "no test" for "not checked".
#
#   run.session_id is not None / run.job_id is not None
#       ck_agent_runs_scope_binding forbids a steward run with a session or job,
#       so no fixture can construct the state these guard against.
#   run.kind != identity.agent_kind
#       A steward identity is only built for agent_kind='steward'; the DB forbids
#       a non-steward run from carrying a steward_runs row's scope shape.
#   steward_run.steward_job_id != job.id
#       Implied by the claim check plus `db.get(StewardJob, claim_job_id)`.
#   db.get(FamilySpace, identity.space_id) is None
#       steward_jobs.space_id has ON DELETE CASCADE, so the job's space cannot
#       vanish while the job row exists; the space comparison fires first.
#
#   steward_run.steward_job_id != identity.steward_job_id
#       Not isolatable: `uq` on steward_jobs.space_id permits only one active job
#       per space, so a forged job id is either nonexistent (caught by `job is
#       None`), in another space (caught by the space comparison), or terminal
#       (caught by the job-status check). test_steward_fence_rejects_forged_job_id
#       covers the reachable forgery (nonexistent id).


# --------------------------------------------------------------------------
# Cross-kind isolation: the type boundary is the security boundary
# --------------------------------------------------------------------------


def test_execution_from_claims_builds_the_matching_identity():
    assistant = execution_from_claims(
        {
            "agent_kind": "assistant",
            "run_id": 1,
            "job_id": 2,
            "attempt": 1,
            "account_id": 3,
            "space_id": 4,
            "tool_allowlist": [],
        }
    )
    assert isinstance(assistant, ExecutionIdentity)

    steward = execution_from_claims(
        {
            "agent_kind": "steward",
            "run_id": 1,
            "job_id": 2,
            "attempt": 1,
            "space_id": 4,
            "tool_allowlist": [],
        }
    )
    assert isinstance(steward, StewardExecution)
    assert not hasattr(steward, "account_id")


def test_assistant_identity_cannot_admit_a_steward_run(db_session):
    """An assistant-shaped identity must never reach the steward path.

    The steward run has no session, so the assistant fence must reject it rather
    than fall through to any steward logic.
    """
    _, space, _, _, run, _ = _steward_world(db_session)
    identity = ExecutionIdentity(
        run_id=run.id,
        job_id=0,
        expected_attempt=1,
        account_id=1,
        space_id=space.id,
        agent_kind="assistant",
        tool_allowlist=(),
    )
    with pytest.raises(Exception) as exc:
        fence_assistant_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_identity_cannot_admit_an_assistant_run(db_session):
    user, space = create_agent_fixture(db_session, name="fence-assistant")
    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    lease_next(db_session, kind="assistant", leased_by="test-sidecar")

    identity = StewardExecution(
        run_id=run.id,
        steward_job_id=1,
        expected_attempt=1,
        space_id=space.id,
        steward_batch_id=None,
        viewer_account_id=None,
        agent_kind="steward",
        tool_allowlist=(),
    )
    with pytest.raises(Exception) as exc:
        fence_steward_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_dispatcher_routes_by_type_not_by_field(db_session):
    """fence_execution must dispatch on the dataclass type."""
    user, space, job, batch, run, _ = _steward_world(db_session)
    steward_identity = _identity(
        run, job, batch, space_id=space.id, viewer_account_id=user.account.id
    )
    admitted = fence_execution(db_session, steward_identity)
    assert isinstance(admitted[1], StewardRun)

    # An unknown object must be refused, not defaulted to assistant.
    with pytest.raises(Exception) as exc:
        fence_execution(db_session, object())  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


# --------------------------------------------------------------------------
# Assistant fence: one case per check, so removing any single check fails
# --------------------------------------------------------------------------
# The attack model is a **forged run token**, not a corrupted database. Every
# case below keeps the database valid and forges exactly one identity field, so
# exactly one fence check can reject it. If that check is deleted, the case
# stops raising and fails here. This list was derived by mutation testing:
# deleting each `or <check>` in turn and confirming a case fails.
#
# Some comparisons cannot be isolated through the database, and are kept as
# defense in depth (verified by mutation testing — deleting them changes no test
# outcome, which is recorded here rather than hidden):
#   session.account_id / session.space_id / session.agent_kind
#       Blocked by trigger trg_agent_sessions_scope_immutable. Covered as a
#       defense-in-depth assertion in test_session_scope_drift_is_blocked_by_the_database.
#   job.run_id != identity.run_id
#       Implied: job is loaded by identity.job_id and run.job_id must equal it, so
#       a divergent job.run_id means run.job_id != identity.job_id (checked first).
#   job.kind / session.agent_kind != identity.agent_kind
#       Redundant *individually* with run.kind: a non-assistant run cannot carry a
#       session (scope binding) and ck_agent_jobs_kind forbids a steward job row,
#       so the only reachable divergence is a forged agent_kind, which the run.kind
#       comparison rejects first. The three are load-bearing as a **set**:
#       deleting all three together does fail a test (verified by mutation). They
#       are kept as one defensive group because a single comparison is exactly the
#       kind of line a later refactor drops.
# The fence keeps all of them: authorization must not depend on a trigger or a
# CHECK constraint being present in every environment.


def _forged(base: ExecutionIdentity, **overrides) -> ExecutionIdentity:
    fields = {
        "run_id": base.run_id,
        "job_id": base.job_id,
        "expected_attempt": base.expected_attempt,
        "account_id": base.account_id,
        "space_id": base.space_id,
        "agent_kind": base.agent_kind,
        "tool_allowlist": base.tool_allowlist,
    }
    fields.update(overrides)
    return ExecutionIdentity(**fields)  # type: ignore[arg-type]


def test_assistant_fence_admits_a_leased_run(db_session):
    _, _, run, identity = _assistant_world(db_session)
    admitted_run, admitted_session, admitted_job = fence_assistant_execution(db_session, identity)
    assert admitted_run.id == run.id
    assert admitted_session.id == run.session_id
    assert admitted_job.id == run.job_id


def test_assistant_fence_rejects_forged_attempt(db_session):
    _, _, _, identity = _assistant_world(db_session)
    _assert_scope_mismatch(
        db_session, _forged(identity, expected_attempt=identity.expected_attempt + 1)
    )


def test_assistant_fence_rejects_forged_job_id(db_session):
    """A token naming someone else's job must not admit this run."""
    _, _, _, identity = _assistant_world(db_session)
    _assert_scope_mismatch(db_session, _forged(identity, job_id=identity.job_id + 999))


def test_assistant_fence_rejects_forged_account(db_session):
    _, _, _, identity = _assistant_world(db_session)
    other, _ = create_agent_fixture(db_session, name="fence-forge-account")
    _assert_scope_mismatch(db_session, _forged(identity, account_id=other.account.id))


def test_assistant_fence_rejects_forged_space(db_session):
    _, _, _, identity = _assistant_world(db_session)
    _, other_space = create_agent_fixture(db_session, name="fence-forge-space")
    _assert_scope_mismatch(db_session, _forged(identity, space_id=other_space.id))


def test_assistant_fence_rejects_drifted_job_account(db_session):
    """The queue job's account is an independent binding from the session's."""
    from app.models.agent import AgentJob

    _, _, run, identity = _assistant_world(db_session)
    other, _ = create_agent_fixture(db_session, name="fence-drift-job-account")
    db_session.get(AgentJob, run.job_id).account_id = other.account.id
    db_session.commit()
    _assert_scope_mismatch(db_session, identity)


def test_assistant_fence_rejects_drifted_job_space(db_session):
    """The queue job's space is an independent binding from the session's."""
    from app.models.agent import AgentJob

    _, _, run, identity = _assistant_world(db_session)
    _, other_space = create_agent_fixture(db_session, name="fence-drift-job-space")
    db_session.get(AgentJob, run.job_id).space_id = other_space.id
    db_session.commit()
    _assert_scope_mismatch(db_session, identity)


def test_assistant_fence_rejects_forged_allowlist(db_session):
    _, _, _, identity = _assistant_world(db_session)
    _assert_scope_mismatch(db_session, _forged(identity, tool_allowlist=("familygraph.echo",)))


def test_assistant_fence_rejects_forged_kind(db_session):
    """A steward-shaped token must not admit an assistant run."""
    _, _, _, identity = _assistant_world(db_session)
    _assert_scope_mismatch(db_session, _forged(identity, agent_kind="steward"))


def test_assistant_fence_rejects_unknown_run(db_session):
    _, _, _, identity = _assistant_world(db_session)
    _assert_scope_mismatch(db_session, _forged(identity, run_id=identity.run_id + 999))


def test_assistant_fence_rejects_drifted_run_allowlist(db_session):
    """Server-side drift (not just a forged token) is caught too."""
    _, _, run, identity = _assistant_world(db_session)
    run.tool_allowlist_json = ["familygraph.echo"]
    db_session.commit()
    _assert_scope_mismatch(db_session, identity)


def test_assistant_fence_rejects_drifted_run_attempt(db_session):
    _, _, run, identity = _assistant_world(db_session)
    run.attempt += 1
    db_session.commit()
    _assert_scope_mismatch(db_session, identity)


def test_assistant_fence_rejects_run_without_a_job(db_session):
    """A run whose queue job link is gone is not executable."""
    _, _, run, identity = _assistant_world(db_session)
    run.job_id = None
    db_session.commit()
    _assert_scope_mismatch(db_session, identity)


def test_assistant_fence_rejects_drifted_job_attempt(db_session):
    from app.models.agent import AgentJob

    _, _, run, identity = _assistant_world(db_session)
    db_session.get(AgentJob, run.job_id).attempt += 1
    db_session.commit()
    _assert_scope_mismatch(db_session, identity)


def test_session_scope_drift_is_blocked_by_the_database(db_session):
    """Defense-in-depth layer: the DB itself forbids session scope drift.

    This is why the fence's session account/space/kind comparisons have no
    forged-token counterpart — the drift they compare against cannot be
    produced while the trigger exists.
    """
    _, _, run, identity = _assistant_world(db_session)
    other, other_space = create_agent_fixture(db_session, name="fence-trigger")
    session_row = db_session.get(AgentSession, run.session_id)

    for field, value in (
        ("account_id", other.account.id),
        ("space_id", other_space.id),
        ("agent_kind", "steward"),
    ):
        original = getattr(session_row, field)
        setattr(session_row, field, value)
        with pytest.raises(sa.exc.IntegrityError):
            db_session.commit()
        db_session.rollback()
        session_row = db_session.get(AgentSession, run.session_id)
        assert getattr(session_row, field) == original, f"{field} was mutable"

    # And the fence still admits the untouched run, proving the rollbacks above
    # did not leave the fixture in a broken state.
    assert fence_assistant_execution(db_session, identity)[0].id == run.id


def test_assistant_fence_still_enforces_membership(db_session):
    """Regression guard: the assistant fence keeps its own account-based check."""
    user, space = create_agent_fixture(db_session, name="fence-assistant-member")
    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    lease_next(db_session, kind="assistant", leased_by="test-sidecar")
    job_id = db_session.query(AgentRun).get(run.id).job_id

    member = db_session.query(SpaceMember).filter_by(space_id=space.id, user_id=user.id).one()
    member.status = "removed"
    db_session.commit()

    identity = ExecutionIdentity(
        run_id=run.id,
        job_id=job_id,
        expected_attempt=1,
        account_id=user.account.id,
        space_id=space.id,
        agent_kind="assistant",
        tool_allowlist=(),
    )
    with pytest.raises(Exception) as exc:
        fence_assistant_execution(db_session, identity)
    assert exc.value.status_code == 403
    assert _error(exc)["code"] == AGENT_TOKEN_SCOPE_MISMATCH


def test_steward_scope_binding_is_enforced_by_the_database(db_session):
    """The DB, not just the service, forbids a session on a steward run."""
    _, space, job, batch, _, _ = _steward_world(db_session)
    user, _ = create_agent_fixture(db_session, name="fence-session-forger")
    session_row = AgentSession(
        account_id=user.account.id,
        space_id=space.id,
        agent_kind="assistant",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(session_row)
    db_session.commit()

    now = utcnow()
    db_session.add(
        AgentRun(
            session_id=session_row.id,
            kind="steward",
            status="queued",
            attempt=0,
            max_attempts=1,
            policy_version="p1",
            tool_allowlist_json=[],
            created_at=now,
            updated_at=now,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_steward_job_cannot_enter_the_generic_queue(db_session):
    """The queue red line: enqueue_run refuses kind='steward'."""
    user, space = create_agent_fixture(db_session, name="fence-queue")
    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    with pytest.raises(Exception) as exc:
        enqueue_run(
            db_session,
            agent_session=session_row,
            kind="steward",
            policy_version="p1",
            tool_allowlist=[],
        )
    assert exc.value.status_code == 422
    assert _error(exc)["code"] == "AGENT_KIND_UNSUPPORTED"


def test_account_model_is_unused_by_the_steward_fence(db_session):
    """Sanity: the steward world really has no account binding on the run."""
    _, _, _, _, run, _ = _steward_world(db_session)
    assert run.session_id is None
    assert run.job_id is None
    assert db_session.query(Account).count() >= 1


def test_steward_prompt_version_is_the_asserted_literal():
    """Cross-side literal, asserted verbatim.

    The prompt text moved into the sidecar image, so this constant — not a hash
    of server-side text — is what anchors evaluation reports and what the sidecar
    compares against. Renaming it on one side only must fail a test rather than
    reject every steward run at runtime.
    """
    from app.services.steward_assist import STEWARD_PROMPT_VERSION

    assert STEWARD_PROMPT_VERSION == "steward-v1"
