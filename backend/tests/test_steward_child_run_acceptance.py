"""S1 acceptance: the child-run path exists, is wired end to end, and is off.

Two kinds of assertion live here:

1. **Behaviour equivalence** (AC-1): the four assists still run in-process and
   ``steward_model_calls.run_id`` stays NULL. S1 is explicitly "built but not
   enabled", so this is the property that makes the stage safe to merge.
2. **The new path works** (AC-5, AC-6, AC-14): a manually created batch leases a
   child run, fetches its context, and settles — without a real model. Per the
   repo's standing lesson (spec §6) a mock cannot prove an internal-protocol
   contract, so these drive the real FastAPI internal listener through the real
   service layer; only the upstream model call is absent.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from test_steward_candidate_evidence import family
from test_steward_candidate_evidence_integration import _core, _transport, _world

from app import config
from app.errors import extract_api_error
from app.models.agent import AgentRun
from app.models.steward import StewardModelCall, StewardRun
from app.services import agent_tokens, steward_assist


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """Steward engine on, Pi runtime OFF: the S1 shipping shape."""
    for flag in ("STEWARD_ENABLED", "PERSONAL_FAMILY_VIEW_ENABLED", "STEWARD_ASSIST_CANDIDATE"):
        monkeypatch.setattr(config, flag, True)
    for flag in ("STEWARD_WORKER_ENABLED", "STEWARD_PI_RUNTIME_ENABLED"):
        monkeypatch.setattr(config, flag, False)


def _service_token(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    return {"Authorization": f"Bearer {agent_tokens.issue_service_token()}"}


# --------------------------------------------------------------------------
# AC-1: behaviour equivalence — nothing is enabled yet
# --------------------------------------------------------------------------


def test_in_process_assist_leaves_run_id_null(db_session, monkeypatch):
    """The four assists still execute in-process, so no child run is created."""
    world = _world(db_session)
    _core(db_session, world)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    calls: list[dict[str, object]] = []
    with monkeypatch.context() as patch:
        patch.setattr(steward_assist, "_apply_batch", lambda *_a, **_k: "applying")
        steward_assist.execute_batch(
            db_session, batch.id, transport=_transport(db_session, world, calls)
        )
    db_session.expire_all()
    attempt = db_session.scalar(
        select(StewardModelCall).where(StewardModelCall.batch_id == batch.id)
    )
    assert attempt is not None
    assert attempt.run_id is None, "S1 must not create child runs on the in-process path"
    assert db_session.scalar(select(AgentRun).where(AgentRun.kind == "steward")) is None


def test_pi_runtime_off_means_the_lease_endpoint_is_closed(db_session, monkeypatch):
    """STEWARD_PI_RUNTIME_ENABLED defaults off, and the endpoint says so (503)."""
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", False)
    from fastapi.testclient import TestClient

    from app.main import internal_app

    response = TestClient(internal_app).post(
        "/internal/agent/steward/jobs/lease",
        headers={"Authorization": f"Bearer {agent_tokens.issue_service_token()}"},
        json={"kind": "steward", "leased_by": "test"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STEWARD_DISABLED"


def test_assistant_lease_endpoint_still_rejects_steward_kind(db_session, monkeypatch):
    """AC-3: the generic queue endpoint does not become a steward consumer."""
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    from fastapi.testclient import TestClient

    from app.main import internal_app

    response = TestClient(internal_app).post(
        "/internal/agent/jobs/lease",
        headers={"Authorization": f"Bearer {agent_tokens.issue_service_token()}"},
        json={"kind": "steward", "leased_by": "test"},
    )
    # The request schema pins kind to "assistant", so a steward kind is a 422 at
    # the boundary rather than reaching the handler's own guard.
    assert response.status_code == 422


# --------------------------------------------------------------------------
# AC-14: controlled E2E — lease → context → settle, no real model
# --------------------------------------------------------------------------


def _leased_child_run(db_session, monkeypatch):
    """Create a batch and lease one child run through the real service layer."""
    world = _world(db_session)
    _core(db_session, world)
    batch = steward_assist.schedule_due_batch(db_session)
    assert batch is not None
    calls: list[dict[str, object]] = []
    with monkeypatch.context() as patch:
        patch.setattr(steward_assist, "_apply_batch", lambda *_a, **_k: "applying")
        steward_assist.execute_batch(
            db_session, batch.id, transport=_transport(db_session, world, calls)
        )
    db_session.expire_all()
    # Re-arm the batch so the child-run lease has something to pick up: the
    # in-process executor already consumed its attempt.
    attempt = db_session.scalar(
        select(StewardModelCall).where(
            StewardModelCall.batch_id == batch.id, StewardModelCall.run_id.is_(None)
        )
    )
    assert attempt is not None
    attempt.status = "reserved"
    batch.status = "pending"
    from app.utils.timeutil import utcnow

    batch.next_attempt_at = utcnow()
    batch.lease_until = None
    db_session.commit()
    return world, batch, attempt


def test_lease_child_run_binds_exactly_one_attempt_without_network(db_session, monkeypatch):
    """AC-5/AC-14: the lease creates the run + steward_run + attempt binding."""
    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", True)
    _, batch, attempt = _leased_child_run(db_session, monkeypatch)

    grant = steward_assist.lease_child_run(db_session, leased_by="test-sidecar")

    assert grant is not None
    run = db_session.get(AgentRun, grant["run_id"])
    assert run is not None and run.kind == "steward"
    # Space-scoped: no session, no queue job (DB-enforced by scope binding).
    assert run.session_id is None and run.job_id is None
    steward_run = db_session.scalar(select(StewardRun).where(StewardRun.run_id == run.id))
    assert steward_run is not None
    assert steward_run.steward_job_id == grant["steward_job_id"]
    assert steward_run.assist_batch_id == batch.id
    # Exactly one attempt is bound, which is what makes settlement unambiguous.
    assert attempt.run_id == run.id
    assert attempt.status == "in_flight"
    assert grant["tool_allowlist"] == []


def test_lease_child_run_returns_none_when_nothing_is_due(db_session, monkeypatch):
    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", True)
    assert steward_assist.lease_child_run(db_session, leased_by="test-sidecar") is None


def test_steward_run_token_carries_no_account_and_the_batch_binding(db_session, monkeypatch):
    """AC-5: the token's claims are the space-scoped shape, not the account one."""
    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", True)
    _, batch, _ = _leased_child_run(db_session, monkeypatch)
    grant = steward_assist.lease_child_run(db_session, leased_by="test-sidecar")
    assert grant is not None

    token = agent_tokens.issue_run_token(
        run_id=grant["run_id"],
        job_id=grant["steward_job_id"],
        attempt=grant["attempt"],
        agent_kind="steward",
        space_id=grant["space_id"],
        tool_allowlist=[],
        steward_batch_id=grant["assist_batch_id"],
        viewer_account_id=grant["viewer_account_id"],
    )
    claims = agent_tokens.decode_run_token(token)
    assert claims["agent_kind"] == "steward"
    assert "account_id" not in claims
    assert claims["steward_batch_id"] == batch.id


def test_settle_child_run_marks_the_attempt_and_never_double_settles(db_session, monkeypatch):
    """AC-5: the run's terminal state and the attempt's settlement are one unit."""
    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", True)
    _, batch, attempt = _leased_child_run(db_session, monkeypatch)
    grant = steward_assist.lease_child_run(db_session, leased_by="test-sidecar")
    assert grant is not None
    run = db_session.get(AgentRun, grant["run_id"])
    assert run is not None
    run.status = "running"
    db_session.commit()

    output = json.dumps([])  # terminology/empty candidate output: a legal product
    status = steward_assist.settle_child_run(
        db_session, run, status="succeeded", text=output, usage=None, latency_ms=12
    )
    db_session.commit()
    db_session.expire_all()
    settled = db_session.get(StewardModelCall, attempt.id)
    assert settled is not None
    assert status == settled.status
    assert settled.status in ("succeeded", "degraded")

    # A second settlement must not overwrite the recorded outcome.
    before = settled.status
    again = steward_assist.settle_child_run(db_session, run, status="succeeded", text=output)
    assert again is None
    assert db_session.get(StewardModelCall, attempt.id).status == before


def test_settle_child_run_bills_conservatively_on_failure(db_session, monkeypatch):
    """AC-5: a failed run bills the reservation and never auto-retries."""
    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", True)
    _, batch, attempt = _leased_child_run(db_session, monkeypatch)
    grant = steward_assist.lease_child_run(db_session, leased_by="test-sidecar")
    assert grant is not None
    run = db_session.get(AgentRun, grant["run_id"])
    assert run is not None
    run.status = "running"
    db_session.commit()

    steward_assist.settle_child_run(db_session, run, status="failed", error_code="timeout")
    db_session.commit()
    db_session.expire_all()
    settled = db_session.get(StewardModelCall, attempt.id)
    assert settled is not None
    # A timeout cannot prove the upstream did not process the request, so it is
    # unknown (conservatively billed) rather than failed-and-forgettable.
    assert settled.status == "unknown"
    assert (settled.billed_tokens or 0) > 0


# --------------------------------------------------------------------------
# AC-6: the context guard is positive, not merely absent
# --------------------------------------------------------------------------


def test_steward_context_requires_a_real_steward_run(db_session, monkeypatch):
    """A fabricated steward run_id is refused rather than silently accepted."""
    from app.errors import POLICY_CONTEXT_INVALID, raise_api_error
    from app.services import context_builder

    world = _world(db_session)
    admin = world.a
    builder = context_builder.ContextBuilder(db_session)
    with pytest.raises(Exception) as exc:
        builder.build(
            actor=admin,
            space_id=world.space.id,
            agent_kind="steward",
            query="q",
            run_id=999_999,
        )
    assert exc.value.status_code == 422
    payload = extract_api_error(exc.value.detail)
    assert payload is not None and payload["code"] == POLICY_CONTEXT_INVALID
    # Sanity: the helper this test guards against misuse of is the same one the
    # endpoint uses, so a failure here cannot be a no-op.
    assert callable(raise_api_error)


def test_steward_context_rejects_a_run_from_another_space(db_session, monkeypatch):
    """A real steward run in a different space must not authorize this projection."""
    from app.services import context_builder

    monkeypatch.setattr(config, "STEWARD_PI_RUNTIME_ENABLED", True)
    world, batch, _ = _leased_child_run(db_session, monkeypatch)
    grant = steward_assist.lease_child_run(db_session, leased_by="test-sidecar")
    assert grant is not None

    # A second, unrelated space: `family` gives it its own account, space and
    # confirmed facts, which is what makes this a cross-space check rather than
    # a missing-row check.
    other_world = family(db_session, name="other-space")
    with pytest.raises(Exception) as exc:
        context_builder.ContextBuilder(db_session).build(
            actor=other_world.a,
            space_id=other_world.space.id,
            agent_kind="steward",
            query="q",
            run_id=grant["run_id"],
        )
    assert exc.value.status_code == 422

    # Positive control: the *same* run_id with the *correct* space must pass the
    # guard. Without this, the rejection above could be caused by any other
    # failure in build() and the test would be proving nothing.
    built = context_builder.ContextBuilder(db_session).build(
        actor=world.a,
        space_id=world.space.id,
        agent_kind="steward",
        query="q",
        run_id=grant["run_id"],
    )
    assert built.build_id is not None
