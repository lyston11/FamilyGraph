"""E3 acceptance: terminology runs through the Pi child run carrier.

These tests were written to prove carrier equivalence — that the Pi path asked the
same question and wrote back the same product as the in-process path. That
equivalence was established before the cutover, and the in-process path is now
gone, so what remains here is the part that is still load-bearing: the Pi chain's
own contracts (gateway reachability, egress audit, crash convergence, a stranded
lease converging) and the terminology fences that must hold on it.

The chain is driven for real: lease → child run → context projection → settle, over
HTTP against the internal app. A mock cannot prove an internal-protocol contract
(this repo has already paid for that lesson twice), so only the upstream model call
is absent: the product is what a model would have returned, handed to settlement the
way the sidecar hands it over.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from test_steward_terminology import (
    _drain,
    _enable_provider,
    _grandchild_family,
)

from app import config
from app.models.agent import AgentRun
from app.models.audit_log import AuditLog
from app.models.steward import CARRIER_PI, StewardModelCall
from app.services import agent_tokens, provider_proxy, steward_assist, terms

# --------------------------------------------------------------------------
# Driving one attempt through the Pi chain
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """Steward engine and the Pi runtime on; the carrier is set per test."""
    for flag in ("STEWARD_ENABLED", "STEWARD_PI_RUNTIME_ENABLED", "AGENT_RUNTIME_ENABLED"):
        monkeypatch.setattr(config, flag, True)
    # Terminology only: the other kinds would add attempts whose products this
    # file does not reason about, and the assertion "the plan has a terminology
    # attempt" would stop meaning what it says.
    for kind in ("CANDIDATE", "RANKING", "EXPLANATION"):
        monkeypatch.setattr(config, f"STEWARD_ASSIST_{kind}", False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_TERMINOLOGY", True)
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _world_with_terminology(db_session):
    """A space whose core job registers a terminology attempt."""
    terms.seed_builtin_packs(db_session)
    db_session.commit()
    space, _gc, _mom, _gm = _grandchild_family(db_session)
    _enable_provider(db_session, space)
    _drain(db_session, space)
    return space


def _lease_and_open(db_session, *, carrier: str, kind: str = "terminology"):
    """Lease one attempt of `kind` and open its child run. Returns (grant, run, token)."""
    from fastapi.testclient import TestClient

    from app.main import internal_app

    plan = db_session.scalar(
        select(steward_assist.StewardAssistPlan).order_by(
            steward_assist.StewardAssistPlan.id.desc()
        )
    )
    assert plan is not None
    attempt = db_session.scalar(
        select(StewardModelCall).where(
            StewardModelCall.plan_id == plan.id,
            StewardModelCall.assist_kind == kind,
        )
    )
    assert attempt is not None, f"the plan has no {kind} attempt"
    assert attempt.carrier == carrier

    grant = steward_assist.lease_attempt(
        db_session, space_id=plan.space_id, worker_id="e3-sidecar", carrier=carrier
    )
    assert grant is not None
    assert grant["assist_kind"] == kind
    run = steward_assist.open_child_run(
        db_session, attempt_id=grant["attempt_id"], lease_owner="e3-sidecar"
    )
    assert run is not None
    token = agent_tokens.issue_run_token(
        run_id=run.id,
        job_id=grant["steward_job_id"],
        attempt=run.attempt,
        agent_kind="steward",
        space_id=grant["space_id"],
        tool_allowlist=list(run.tool_allowlist_json or []),
        steward_attempt_id=grant["attempt_id"],
        viewer_account_id=grant["viewer_account_id"],
    )
    db_session.commit()
    return grant, run, token, TestClient(internal_app)


def _context(client, run_id: int, token: str):
    response = client.get(
        f"/internal/agent/runs/{run_id}/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _settle(client, run_id: int, token: str, output: str):
    return client.post(
        f"/internal/agent/runs/{run_id}/settle",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "succeeded", "output_text": output},
    )


def _terminology_product(payload: dict) -> str:
    """A product the server's closed schema accepts, built from the projection.

    Built from the projection's own targets rather than hard-coded: the term must
    come from that target's ``allowed_terms``, be no longer than its baseline and
    differ from it, and carry the server's own ``concept_code``. Inventing a shape
    the validator happens to accept would make the test prove nothing about the
    contract the carriers must both satisfy.
    """
    items = []
    for target in payload["targets"]:
        baseline = target["baseline_term"]
        term = next(
            (
                candidate
                for candidate in target["allowed_terms"]
                if candidate != baseline and len(candidate) <= len(baseline)
            ),
            None,
        )
        assert term is not None, f"no usable term for {target['target_ref']}"
        items.append(
            {
                "target_ref": target["target_ref"],
                "concept_code": target["concept_code"],
                "term": term,
                "reason_code": "synonym",
            }
        )
    return json.dumps({"version": 1, "context_hash": payload["context_hash"], "items": items})


# --------------------------------------------------------------------------
# Carrier equivalence
# --------------------------------------------------------------------------


def test_a_pi_settlement_cannot_write_back_after_the_viewer_is_revoked(db_session, monkeypatch):
    """A revoked viewer's product must not be applied.

    Carrier equivalence would be worthless if the Pi path applied products without
    the authorization the in-process path uses. Revoking the viewer between the send
    and the settlement is exactly the window that matters.

    The refusal arrives one layer earlier than the write-back fence — the internal
    request itself is denied, because every run-scoped request recomputes the
    membership judgement. That is stronger than the fence, and it means the attempt
    stays in flight until recovery converges it rather than being applied.
    """
    from app.models.account import Account
    from app.models.space import SpaceMember

    _world_with_terminology(db_session)
    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI)
    payload = json.loads(_context(client, run.id, token)["context_blocks"][0]["content"])

    # Revoke the viewer after the projection was handed over: the product is now
    # computed against a world that no longer exists.
    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None and attempt.viewer_account_id is not None
    viewer = db_session.get(Account, attempt.viewer_account_id)
    assert viewer is not None
    member = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == grant["space_id"], SpaceMember.user_id == viewer.user_id
        )
    )
    assert member is not None
    member.status = "removed"
    db_session.commit()

    response = _settle(client, run.id, token, _terminology_product(payload))
    # The refusal is a scope violation (403), not a missing run: the steward run is
    # real and the token is its own, but the viewer's membership is gone, so the
    # execution identity no longer resolves.
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "AGENT_TOKEN_SCOPE_MISMATCH"
    db_session.expire_all()

    # Nothing was applied, and the row is not silently left live forever: recovery
    # converges it within one maintenance tick (the documented bound).
    held = db_session.get(StewardModelCall, grant["attempt_id"])
    assert held is not None
    assert held.applied_at is None
    assert held.status == "in_flight"
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    held.lease_until = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert steward_assist.recover_stuck_attempts(db_session) >= 1
    db_session.expire_all()
    assert db_session.get(StewardModelCall, grant["attempt_id"]).status == "unknown"


def test_a_pi_settlement_is_audited_against_the_child_run(db_session, monkeypatch):
    """The product reaches the ledger through the run, so the run is the audit target.

    A Pi attempt's model call happens inside the gateway, which audits egress
    against the run id. Asserting the linkage here keeps "one egress, one audit"
    true for the steward path as well as the assistant path.
    """
    _world_with_terminology(db_session)
    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI)

    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    # The attempt points at the run it is executed by, which is what the gateway
    # audits against and what makes the reverse lookup one indexed read.
    assert attempt.run_id == run.id
    child = db_session.get(AgentRun, run.id)
    assert child is not None and child.kind == "steward"
    assert child.session_id is None and child.job_id is None


# --------------------------------------------------------------------------
# Crash convergence
# --------------------------------------------------------------------------


def test_a_stuck_pi_child_run_converges_without_a_second_call(db_session, monkeypatch):
    """A sidecar killed mid-run must not leave the attempt live or re-send it.

    Crash point ⑤: the child run exists and the lease expires. Nothing proves
    whether the upstream processed the request, so the attempt converges to
    ``unknown`` with conservative billing and is never replayed (memory #407).
    """
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    _world_with_terminology(db_session)
    grant, run, _token, _client = _lease_and_open(db_session, carrier=CARRIER_PI)

    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    reserved_terminology = len(
        list(
            db_session.scalars(
                select(StewardModelCall).where(
                    StewardModelCall.plan_id == attempt.plan_id,
                    StewardModelCall.assist_kind == "terminology",
                )
            )
        )
    )
    attempt.lease_until = utcnow() - timedelta(seconds=1)
    child = db_session.get(AgentRun, run.id)
    assert child is not None
    child.lease_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert steward_assist.recover_stuck_attempts(db_session) >= 1
    assert steward_assist.recover_stuck_child_runs(db_session) >= 1

    db_session.expire_all()
    converged = db_session.get(StewardModelCall, grant["attempt_id"])
    assert converged is not None
    assert converged.status == "unknown"
    assert converged.error_code == steward_assist.REASON_NETWORK_UNKNOWN
    # Conservative billing: the reservation is charged, because the request may
    # have reached the upstream.
    assert converged.billed_tokens == (converged.reserved_input_tokens or 0) + (
        converged.reserved_output_tokens or 0
    )
    # The run is cancelled rather than left leased, so nothing can resume it.
    cancelled = db_session.get(AgentRun, run.id)
    assert cancelled is not None and cancelled.cancel_requested is True
    # And nothing was replayed: unknown is terminal, so the plan's attempt rows are
    # exactly the ones it reserved. A retry would show up as a new row.
    assert (
        len(
            list(
                db_session.scalars(
                    select(StewardModelCall).where(
                        StewardModelCall.plan_id == attempt.plan_id,
                        StewardModelCall.assist_kind == "terminology",
                    )
                )
            )
        )
        == reserved_terminology
    )


def test_a_stranded_lease_converges_within_one_lease_period(db_session, monkeypatch):
    """A leased attempt nobody finishes must converge, not sit ``in_flight`` forever.

    The carrier no longer has a rollback target (there is only one), but the
    property it protected is unchanged and is what matters operationally: a
    sidecar that dies holding a lease must not leave work stranded. Convergence is
    the recovery path's job, and this asserts it still happens.
    """
    from datetime import timedelta

    from app.utils.timeutil import utcnow

    _world_with_terminology(db_session)
    grant, run, _token, _client = _lease_and_open(db_session, carrier=CARRIER_PI)

    # The stranded attempt itself is not handed out again while its lease is live:
    # the lease, not the carrier, is what stops a second executor taking it. (A
    # different attempt of the same space may still be leaseable — the per-space
    # budget allows it — so the assertion names the attempt rather than the space.)
    stranded = db_session.get(StewardModelCall, grant["attempt_id"])
    assert stranded is not None and stranded.status == "in_flight"
    assert stranded.lease_owner == "e3-sidecar"
    second = steward_assist.lease_attempt(
        db_session,
        space_id=grant["space_id"],
        worker_id="second-sidecar",
        carrier=CARRIER_PI,
    )
    assert (
        second is None or second["attempt_id"] != grant["attempt_id"]
    ), "a live lease must not be handed to a second executor"

    # Once the lease expires, recovery converges both rows: the attempt to
    # ``unknown`` and the run to a terminal state. Nothing else would adjudicate
    # the run — the assistant reaper selects AgentJob, and a steward run has none.
    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    attempt.lease_until = utcnow() - timedelta(seconds=1)
    child = db_session.get(AgentRun, run.id)
    assert child is not None
    child.lease_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert steward_assist.recover_stuck_attempts(db_session) >= 1
    assert steward_assist.recover_stuck_child_runs(db_session) >= 1

    db_session.expire_all()
    assert db_session.get(StewardModelCall, grant["attempt_id"]).status == "unknown"
    assert db_session.get(AgentRun, run.id).status == "expired"


# --------------------------------------------------------------------------
# The gateway is the single egress, for both kinds
# --------------------------------------------------------------------------


def test_a_steward_child_run_can_reach_the_provider_gateway(db_session, monkeypatch):
    """A child run must be able to make a model call through the only egress.

    The gateway used to authorize with the assistant-only alias, so every steward
    request was refused with AGENT_TOKEN_SCOPE_MISMATCH. That is not a cosmetic
    gap: the gateway is the single egress, so a child run that cannot reach it has
    no way to call a model at all — and its traffic would go unaudited, since the
    egress audit is written there.

    It also resolved the provider with the default kind, so even once authorized a
    steward run looked up the assistant's space settings and reported the provider
    as unavailable. Both are asserted by reaching a *later* check: an authorization
    or resolution failure produces 403/503, while the request being rejected for its
    body means both succeeded.
    """
    _world_with_terminology(db_session)
    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI)

    # The model name must match the run's pinned snapshot, or the gateway rejects
    # the body before sending. Read it from the snapshot rather than assuming.
    child = db_session.get(AgentRun, run.id)
    assert child is not None
    snapshot = child.runtime_snapshot_json or {}
    assert snapshot.get("model"), "the child run must pin its provider snapshot"

    response = client.post(
        f"/internal/agent/runs/{run.id}/provider/responses",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": snapshot["model"], "input": [], "stream": False},
    )
    # Authorization and resolution passed; what remains is the upstream call, which
    # this test does not make (there is no provider at the configured URL).
    assert response.status_code not in (403, 503), response.text
    assert "AGENT_TOKEN_SCOPE_MISMATCH" not in response.text
    assert "Provider 当前不可用" not in response.text


def test_an_assistant_token_still_cannot_reach_a_steward_run(db_session, monkeypatch):
    """Opening the gateway to both kinds must not blur the two identities.

    Each kind is still authorized by its own check set, so an assistant token
    aimed at a steward run is refused — the gateway is not a hole in the scope
    boundary, it is the same boundary with one more admitted caller.

    The refusal is 404 rather than 403 by design: the run is not visible to an
    assistant identity at all, and the assistant authorizer checks the kind before
    touching the session (a steward run has none, so reading it first would turn a
    scope violation into an assertion failure).
    """
    _world_with_terminology(db_session)
    _grant, run, _token, client = _lease_and_open(db_session, carrier=CARRIER_PI)

    from app.services import agent_tokens as tokens

    # An assistant token: its claims say assistant, so the steward authorizer must
    # reject it even though the run id is real.
    assistant_token = tokens.issue_run_token(
        run_id=run.id,
        job_id=1,
        attempt=1,
        agent_kind="assistant",
        account_id=1,
        space_id=1,
        tool_allowlist=[],
    )
    response = client.post(
        f"/internal/agent/runs/{run.id}/provider/responses",
        headers={"Authorization": f"Bearer {assistant_token}"},
        json={"model": "x", "input": [], "stream": False},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "AGENT_RUN_NOT_FOUND"


def test_a_steward_egress_is_audited_against_its_child_run(db_session, monkeypatch):
    """The one model call a child run makes is audited, with its safe classification.

    Routing the model call through the gateway is what buys the audit — but only if the row
    is written against the child run, since that is the id the operator has and the
    only link back to the attempt. `error_class`/`retryable`/`sent` must be present
    too: they are what makes a failure diagnosable without reading prompt text.
    """
    from test_provider_proxy import _FakeAsyncClient, _FakeUpstream

    _world_with_terminology(db_session)
    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI)

    # Serve the upstream from a fake so no network call happens; the point is the
    # audit, not the model.
    _FakeAsyncClient.response = _FakeUpstream([b'{"id": "cmpl-steward"}'])
    _FakeAsyncClient.raise_on_send = None
    _FakeAsyncClient.last = None
    _FakeAsyncClient.send_delay_seconds = 0.0
    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _FakeAsyncClient)

    child = db_session.get(AgentRun, run.id)
    assert child is not None
    model = (child.runtime_snapshot_json or {})["model"]

    response = client.post(
        f"/internal/agent/runs/{run.id}/provider/responses",
        headers={"Authorization": f"Bearer {token}"},
        # Streaming is mandatory for the gateway (it proxies an SSE stream), so a
        # non-streaming body is refused before the upstream call.
        json={"model": model, "input": [{"role": "user", "content": "x"}], "stream": True},
    )
    assert response.status_code == 200, response.text

    rows = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "agent_provider_egress",
                AuditLog.target_id == run.id,
            )
        )
    )
    assert rows, "a steward model call must be audited against its child run"
    detail = rows[-1].detail or {}
    # The safe classification fields, which the admin latency view also reads.
    assert detail.get("status") == "succeeded"
    assert "bytes_read" in detail
    # Never the prompt or the credential.
    assert grant["assist_kind"] not in (rows[-1].detail_json or "")
    assert "sk-term-test" not in (rows[-1].detail_json or "")


def test_no_remaining_provider_resolution_defaults_to_the_assistant_kind():
    """Every provider resolution in the gateway passes the run's own kind.

    Two of them did not, and each blocked a steward child run one check apart —
    first with AGENT_TOKEN_SCOPE_MISMATCH, then (once authorization was fixed) with
    POLICY_PROVIDER_BLOCKED. They were found one at a time because each fix revealed
    the next, so the invariant is asserted structurally: the default is the assistant
    kind, and a steward run silently reading the assistant's space settings is a
    failure mode that produces a *plausible* answer (cloud forbidden) rather than an
    error, which is why it survived review.

    The two remaining callers that omit the kind are provably assistant-only:
    `run_context` returns early for a steward token, and the citation validator
    requires a session (steward runs have none, by DB constraint).
    """
    import ast
    from pathlib import Path

    from app.services import provider_proxy

    tree = ast.parse(Path(provider_proxy.__file__).read_text(encoding="utf-8"))
    # resolve_for_run(db, run, space_id, agent_kind) and
    # resolve_runtime(db, space_id, *, run, agent_kind): the kind may be passed
    # positionally or by keyword, so accept either.
    required_positional = {"resolve_for_run": 4, "resolve_runtime": 3, "resolve_for_space": 3}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in required_positional:
            continue
        if "agent_kind" in {kw.arg for kw in node.keywords}:
            continue
        if len(node.args) >= required_positional[name]:
            continue
        offenders.append(f"line {node.lineno}: {name} without agent_kind")
    assert offenders == [], (
        "a gateway provider resolution omits the run kind, so a steward run would "
        f"read the assistant's settings: {offenders}"
    )
