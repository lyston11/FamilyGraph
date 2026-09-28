"""Driving Steward attempts through the Pi carrier, for tests.

Before the S5 cutover every Steward test injected a fake upstream transport into
the in-process carrier and let the server send the request itself:

    monkeypatch.setattr(steward_assist, "_post_json", fake)
    steward_assist.execute_plan_attempts(db, plan_id=plan.id, transport=fake)

That seam is gone with the in-process path, so tests drive the same chain the way
production does: the server reserves and leases an attempt, a child run is opened,
the model "answers", and the answer comes back through the real settlement
endpoint. Only the model is absent — ``respond`` plays its part.

The mapping is one-to-one, which is what keeps the migrated tests as strong as
they were:

| in-process seam | here |
|---|---|
| fake transport receives the request payload | ``respond`` sees the real projection |
| fake transport returns response text | ``respond`` returns the text to settle with |
| ``after_send`` mutates the world before write-back | ``between`` runs after the lease |
| ``transport`` raises to fake a failure | ``settle_status`` / ``settle_error_code`` |

``between`` keeps the fence observable: it runs after the send (so the mutation is
not part of the request) and before settlement (so the write-back fence sees the
changed world), which is exactly the window ``after_send`` occupied.

Both carriers' request payloads were verified identical before the cutover
(``test_steward_pi_carrier_terminology``), so assertions written against the
in-process payload carry over unchanged.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.models.steward import CARRIER_PI, StewardAssistPlan, StewardModelCall
from app.services import agent_tokens, steward_assist

#: Receives the lease grant and the parsed context projection; returns the product
#: text a model would have produced, or ``(text, usage)`` when the test also
#: asserts on billing (usage is what the real sidecar reports at settlement).
Responder = Callable[[dict[str, Any], dict[str, Any]], Any]


def _split_answer(answer: Any) -> tuple[str | None, dict[str, int] | None]:
    """Normalize a responder result into (text, usage)."""
    if isinstance(answer, tuple):
        text, usage = answer
        return text, usage
    return answer, None


def _internal_client():
    from fastapi.testclient import TestClient

    from app.main import internal_app

    return TestClient(internal_app)


def lease_and_open(db, *, space_id: int | None = None, worker_id: str = "pi-harness"):
    """Lease one Pi attempt and open its child run.

    Returns ``(grant, run, token, client)``; ``client`` is bound to the internal
    app so callers can hit the real context/settle endpoints.
    """
    grant = steward_assist.lease_attempt(
        db, space_id=space_id, worker_id=worker_id, carrier=CARRIER_PI
    )
    if grant is None:
        return None
    run = steward_assist.open_child_run(db, attempt_id=grant["attempt_id"], lease_owner=worker_id)
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
    db.commit()
    return grant, run, token, _internal_client()


def read_context(client, run_id: int, token: str) -> dict[str, Any]:
    """The projection the sidecar would send, plus the parsed input document.

    A refusal is legitimate here: tests deliberately expire the lease or revoke
    membership between the lease and the read, and the server is then supposed to
    reject. Returns the error body so the caller can assert on it.
    """
    response = client.get(
        f"/internal/agent/runs/{run_id}/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = response.json()
    if response.status_code != 200:
        return {"refused": payload, "parsed": None}
    blocks = payload.get("context_blocks") or []
    parsed = json.loads(blocks[0]["content"]) if blocks else None
    return {**payload, "parsed": parsed}


def settle(
    client,
    run_id: int,
    token: str,
    *,
    status: str = "succeeded",
    output_text: str | None = None,
    error_code: str | None = None,
    usage: dict[str, int] | None = None,
):
    """Settle through the real endpoint, so authorization and fences both run."""
    body: dict[str, Any] = {"status": status}
    if output_text is not None:
        body["output_text"] = output_text
    if error_code is not None:
        body["error_code"] = error_code
    if usage is not None:
        body["usage"] = usage
    return client.post(
        f"/internal/agent/runs/{run_id}/settle",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


_CURRENT_FAKE: Callable[..., dict[str, Any]] | None = None


def use_fake(monkeypatch, fake: Callable[..., dict[str, Any]] | None):
    """Register the upstream fake that harness-driven attempts should answer with.

    Tests used to patch ``steward_assist._post_json`` because the server sent the
    request itself. It no longer does, so the fake is registered here and
    ``drive_attempt`` plays the model's part with it. The fake keeps its original
    signature and therefore keeps observing the same request payload, which is
    what the migrated assertions read.
    """
    monkeypatch.setattr(sys.modules[__name__], "_CURRENT_FAKE", fake)


def current_responder(respond: Responder | None) -> Responder | None:
    """The explicit responder, else the registered fake, else None."""
    if respond is not None:
        return respond
    if _CURRENT_FAKE is None:
        return None
    return responder_from_transport(_CURRENT_FAKE)


def drive_attempt(
    db,
    *,
    space_id: int | None = None,
    respond: Responder | None = None,
    between: Callable[[Any, StewardModelCall], None] | None = None,
    settle_status: str = "succeeded",
    settle_error_code: str | None = None,
    worker_id: str = "pi-harness",
) -> str | None:
    """Lease one attempt, let ``respond`` answer it, settle it. Returns its status.

    ``between`` replaces the old ``after_send`` hook: it runs after the model has
    answered but before write-back, which is the only window where the write-back
    fence can be observed.
    """
    opened = lease_and_open(db, space_id=space_id, worker_id=worker_id)
    if opened is None:
        return None
    grant, run, token, client = opened
    context = read_context(client, run.id, token)
    text: str | None = None
    usage: dict[str, int] | None = None
    effective = current_responder(respond)
    if effective is not None:
        try:
            text, usage = _split_answer(effective(grant, context))
        except Exception as exc:  # noqa: BLE001
            # The deleted in-process carrier caught transport exceptions and turned
            # them into an outcome. Fakes raise the same exceptions to mean the same
            # things, so the translation uses production's own classifier rather
            # than a copy of its rules.
            status, code = steward_assist._classify_transport_error(exc)
            if status == "unknown":
                # "Unknown" is not a status the sidecar may report — it means the
                # outcome is genuinely unknowable, which only a run that died
                # mid-flight can establish. Production reaches it through lease
                # expiry and recovery, so that is the path reproduced here.
                return _expire_and_recover(db, grant, run)
            settle_status = status
            settle_error_code = code
    if between is not None:
        attempt = db.get(StewardModelCall, grant["attempt_id"])
        assert attempt is not None
        between(db, attempt)
    # The sidecar never settles `succeeded` without a product: an empty turn is
    # reported as a failure (worker.ts, PROVIDER_EMPTY_ANSWER), because settling
    # it as success would claim an answer that does not exist. An empty *string*
    # is a different thing — that is a model output the server's validator judges
    # — so only a missing product is rewritten here.
    if settle_status == "succeeded" and text is None:
        settle_status = "failed"
        settle_error_code = "PROVIDER_EMPTY_ANSWER"
    response = settle(
        client,
        run.id,
        token,
        status=settle_status,
        output_text=text if settle_status == "succeeded" else None,
        error_code=settle_error_code,
        usage=usage if settle_status == "succeeded" else None,
    )
    # A refused settlement is a legitimate outcome, not a harness bug: tests
    # deliberately expire the lease or revoke membership between the send and the
    # write-back, and the server is then supposed to refuse. Only an unexpected
    # protocol error is worth failing on.
    if response.status_code != 200:
        body = response.json()
        code = (body.get("error") or {}).get("code")
        assert code in {
            "AGENT_LEASE_EXPIRED",
            "AGENT_TOKEN_SCOPE_MISMATCH",
            "AGENT_RUN_NOT_RUNNING",
        }, response.text
    db.expire_all()
    attempt = db.get(StewardModelCall, grant["attempt_id"])
    # ``unknown`` cannot be settled as a literal status (the settle contract only
    # accepts succeeded/failed), so a classified timeout is reported as a plain
    # failure and the server's own classifier decides the terminal state. Tests
    # that need a genuine ``unknown`` expire the lease instead, which is the only
    # way production reaches it too.
    return attempt.status if attempt is not None else None


def _expire_and_recover(db, grant: dict[str, Any], run: Any) -> str | None:
    """Drive one attempt to ``unknown`` the way production does.

    Expiring both leases and running recovery is the only path that yields
    ``unknown``: the attempt may or may not have been processed upstream, and
    nothing reported an outcome. Settling it as a failure instead would claim
    knowledge the server does not have.
    """
    from datetime import timedelta

    from app.models.agent import AgentRun
    from app.utils.timeutil import utcnow

    attempt = db.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    attempt.lease_until = utcnow() - timedelta(seconds=1)
    child = db.get(AgentRun, run.id)
    if child is not None:
        child.lease_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    steward_assist.recover_stuck_attempts(db)
    steward_assist.recover_stuck_child_runs(db)
    db.expire_all()
    converged = db.get(StewardModelCall, grant["attempt_id"])
    return converged.status if converged is not None else None


def drain_plan(
    db,
    plan_id: int,
    respond: Responder | None = None,
    *,
    between: Callable[[Any, StewardModelCall], None] | None = None,
    rounds: int | None = None,
    worker_id: str = "pi-harness",
) -> str | None:
    """Drive every leaseable Pi attempt of one plan; return the plan's outcome.

    The synchronous analogue of what used to be ``execute_plan_attempts``: the
    loop stops when the plan has nothing left to lease, and the returned value is
    the derived plan outcome callers assert on.
    """
    plan = db.get(StewardAssistPlan, plan_id)
    if plan is None:
        return None
    budget = rounds if rounds is not None else 64
    for _ in range(budget):
        status = drive_attempt(
            db,
            space_id=plan.space_id,
            respond=respond,
            between=between,
            worker_id=worker_id,
        )
        if status is None:
            break
    return steward_assist.plan_outcome(db, plan_id)


def drain_all(
    db,
    respond: Responder | None = None,
    *,
    space_id: int | None = None,
    between: Callable[[Any, StewardModelCall], None] | None = None,
    rounds: int | None = None,
) -> str | None:
    """Drive every due plan; return the last plan's outcome.

    Replaces ``run_due_attempt``, which reported one status for "this round of
    work" — the plan's derived outcome. Returning the last one (rather than a
    list) keeps those assertions meaningful.
    """
    status: str | None = None
    budget = rounds if rounds is not None else 8
    for _ in range(budget):
        plan = steward_assist.schedule_due_attempt(db, space_id=space_id)
        if plan is None:
            break
        status = drain_plan(db, plan.id, respond, between=between)
    return status


def attempts_of(db, plan_id: int, *, kind: str | None = None) -> list[StewardModelCall]:
    """The attempt rows of one plan, optionally filtered to a kind."""
    stmt = select(StewardModelCall).where(StewardModelCall.plan_id == plan_id)
    if kind is not None:
        stmt = stmt.where(StewardModelCall.assist_kind == kind)
    return list(db.scalars(stmt.order_by(StewardModelCall.id)))


def envelope_text(data: dict[str, Any]) -> tuple[str, dict[str, int] | None]:
    """Extract text and usage from a provider response envelope.

    This parsing used to live in the in-process carrier, which read the provider's
    response itself. The Pi path receives the model's text through settlement
    instead, so the sidecar owns the wire parsing now. Tests that fake an upstream
    still produce envelopes, so the extraction lives here rather than disappearing
    with the carrier.

    The protocol is read off the envelope rather than passed in: the old fakes
    returned one shape or the other and several of them switch per request.
    """
    usage: dict[str, int] | None = None
    raw_usage = data.get("usage") or {}
    if "output" in data:
        text_parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text_parts.append(str(part.get("text") or ""))
        text = "".join(text_parts)
        if "input_tokens" in raw_usage or "output_tokens" in raw_usage:
            usage = {
                "prompt_tokens": int(raw_usage.get("input_tokens") or 0),
                "completion_tokens": int(raw_usage.get("output_tokens") or 0),
                "total_tokens": int(raw_usage.get("total_tokens") or 0),
            }
        return text, usage
    choices = data.get("choices") or []
    text = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
    if "total_tokens" in raw_usage or "prompt_tokens" in raw_usage:
        usage = {
            "prompt_tokens": int(raw_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(raw_usage.get("completion_tokens") or 0),
            "total_tokens": int(raw_usage.get("total_tokens") or 0),
        }
    return text, usage


def responder_from_transport(fake: Callable[..., dict[str, Any]]):
    """Adapt an old in-process fake transport into a Pi responder.

    The old seam was ``fake(url, headers, payload, timeout) -> envelope``, where
    ``payload`` carried the projection as the user message. The Pi seam is
    ``respond(grant, context) -> product text``. The projection is the same
    document in both, so the adapter rebuilds a provider-shaped request from the
    context and reduces the envelope back to text.

    Keeping this adapter means migrated tests assert the same things they did
    before: fakes that derive their answer from the request still see the request,
    and their recorded calls keep the same ``payload`` shape.
    """

    def respond(_grant: dict[str, Any], context: dict[str, Any]) -> tuple[str, Any]:
        user_content = json.dumps(context.get("parsed"), ensure_ascii=False)
        payload: dict[str, Any] = {
            "input": [
                {"role": "system", "content": context.get("steward_instructions") or ""},
                {"role": "user", "content": user_content},
            ],
            "messages": [
                {"role": "system", "content": context.get("steward_instructions") or ""},
                {"role": "user", "content": user_content},
            ],
        }
        envelope = fake("https://provider.invalid/pi", {}, payload, 30.0)
        return envelope_text(envelope)

    return respond


def product_responder(products: dict[str, str]) -> Responder:
    """Answer each attempt with the text registered for its assist kind.

    Lets a mixed plan be driven with one call per kind, which is how tests that
    assert "the candidate product landed but the ranking one did not" are written.
    """

    def respond(grant: dict[str, Any], _context: dict[str, Any]) -> str:
        return products[grant["assist_kind"]]

    return respond


__all__ = [
    "Responder",
    "attempts_of",
    "current_responder",
    "drain_all",
    "drain_plan",
    "drive_attempt",
    "envelope_text",
    "lease_and_open",
    "product_responder",
    "read_context",
    "responder_from_transport",
    "settle",
    "use_fake",
]
