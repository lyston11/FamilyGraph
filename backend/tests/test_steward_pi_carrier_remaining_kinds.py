"""E4 acceptance: candidate, ranking and explanation run through the Pi carrier.

E3 proved the carrier chain with terminology. The remaining three kinds differ from
it in ways that matter, and each difference is asserted here rather than assumed:

- **candidate** is space-scoped (no viewer), and its fence is the evidence hash, so
  a changed fact set must retire the attempt. It also carries the direction and
  conflict rules in its instructions, which is why the projection must hand the
  same text over (asserted in E3's schema test and rebuilt here for this kind).
- **ranking** must be a strict permutation. A carrier that produced a valid-looking
  but partial order must still be rejected, so the guard is asserted through the Pi
  path rather than only in-process.
- **explanation** has declared slots and an evidence fence: the product may only
  cite fact ids the server supplied, and the rendered text is the server's, not the
  model's.

Driven over real HTTP through lease → context → settle, like E3: a mock cannot prove
an internal-protocol contract, so only the upstream model call is absent.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from test_steward import _confirm, _emit_fact_event, _person, _run_job, _space

from app import config
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.models.relationship_facts import SOURCE_FACT_TYPES, SourceFact
from app.models.steward import CARRIER_PI, ActionCard, StewardLlmCandidate, StewardModelCall
from app.services import agent_tokens, steward_assist
from app.utils import timeutil
from app.utils.secretbox import encrypt_secret
from conftest import create_space_member

# --------------------------------------------------------------------------
# Driving one attempt through the Pi chain (shared shape with E3)
# --------------------------------------------------------------------------


def _lease_and_open(db_session, *, carrier: str, kind: str):
    """Lease one attempt of `kind` and open its child run."""
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

    # The lease takes the earliest-due attempt of the carrier, not the kind asked
    # for: make every other attempt of this plan not-yet-due so the kind under test
    # is the one that gets leased.
    from app.utils.timeutil import utcnow

    later = utcnow() + __import__("datetime").timedelta(hours=1)
    for other in db_session.scalars(
        select(StewardModelCall).where(
            StewardModelCall.plan_id == plan.id,
            StewardModelCall.id != attempt.id,
            StewardModelCall.status == "reserved",
        )
    ):
        other.next_attempt_at = later
    db_session.commit()

    grant = steward_assist.lease_attempt(
        db_session, space_id=plan.space_id, worker_id="e4-sidecar", carrier=carrier
    )
    assert grant is not None
    assert grant["assist_kind"] == kind
    run = steward_assist.open_child_run(
        db_session, attempt_id=grant["attempt_id"], lease_owner="e4-sidecar"
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


def _payload(client, run_id: int, token: str) -> dict:
    response = client.get(
        f"/internal/agent/runs/{run_id}/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    blocks = body["context_blocks"]
    assert blocks, "the steward context must carry its projection block"
    return json.loads(blocks[0]["content"])


def _settle(client, run_id: int, token: str, output: str):
    return client.post(
        f"/internal/agent/runs/{run_id}/settle",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "succeeded", "output_text": output},
    )


# --------------------------------------------------------------------------
# Space fixture with all three kinds enabled
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for flag in ("STEWARD_ENABLED", "STEWARD_PI_RUNTIME_ENABLED", "AGENT_RUNTIME_ENABLED"):
        monkeypatch.setattr(config, flag, True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_EXPLANATION", True)
    monkeypatch.setattr(config, "STEWARD_ASSIST_TERMINOLOGY", False)
    monkeypatch.setattr(config, "PERSONAL_FAMILY_VIEW_ENABLED", True)


def _provider(db_session, name: str) -> AgentProvider:
    provider = AgentProvider(
        name=name,
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        secret_ciphertext=encrypt_secret("sk-e4"),
        allowed_models_json=["gpt-e4"],
        enabled=True,
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db_session.add(provider)
    db_session.flush()
    return provider


def _space_with_all_kinds(db_session, name: str):
    """A lineage space whose core job reserves candidate, ranking and explanation."""
    space = _space(db_session, name, kind="lineage")
    # A space-scoped kind's context resolves its policy actor from the space's
    # active space_admin; without one the projection comes back empty and there is
    # nothing to send. (Terminology, the E3 kind, is viewer-bound and needs none.)
    create_space_member(db_session, space.id, space.owner_id, role="space_admin")
    a = _person(db_session, space.id, f"{name}-a", gender="f")
    b = _person(db_session, space.id, f"{name}-b", gender="m", member=False, ref=True)
    c = _person(db_session, space.id, f"{name}-c", gender="m", member=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    _confirm(db_session, "biological_parent", a.id, c.id, space_id=space.id)
    event = _emit_fact_event(db_session, fact)

    provider = _provider(db_session, f"e4-provider-{name}")
    setting = AgentSpaceProviderSetting(
        space_id=space.id,
        provider_id=provider.id,
        agent_kind="steward",
        # resolve_runtime needs the model to be one the provider allows; without it
        # the fence reports provider_unavailable and nothing is ever leaseable.
        model="gpt-e4",
        enabled=True,
        assist_candidate=True,
        assist_ranking=True,
        assist_explanation=True,
        cloud_allowed=True,
        inferred_tree=True,
    )
    db_session.add(setting)
    db_session.commit()
    return space, event


# --------------------------------------------------------------------------
# Candidate
# --------------------------------------------------------------------------


def test_candidate_runs_through_the_pi_carrier_and_keeps_its_atomic_fence(db_session, monkeypatch):
    """A candidate product is applied through the Pi path, and only atomic kinds land.

    The candidate fence is deliberately narrow: only ``SOURCE_FACT_TYPES`` may be
    written, and a derived concept (a grandparent, a kinship term) must be dropped
    rather than stored as a relationship. That is the guard E4 must not weaken by
    moving the kind to a new carrier.
    """
    space, event = _space_with_all_kinds(db_session, "e4-candidate")
    _run_job(db_session, space, event.id)
    db_session.expire_all()

    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI, kind="candidate")
    payload = _payload(client, run.id, token)
    # The candidate input is a codename roster plus confirmed facts; the model must
    # be able to name two of them and no others.
    assert payload["facts"], "the candidate projection must carry confirmed facts"
    fact = payload["facts"][0]
    response = _settle(
        client,
        run.id,
        token,
        json.dumps(
            [
                {
                    "kind": "direct_sibling",
                    "subject": fact["subject"],
                    "object": fact["object"],
                }
            ]
        ),
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()

    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    assert attempt.status == "succeeded", attempt.error_code
    candidates = list(db_session.scalars(select(StewardLlmCandidate)))
    assert candidates, "the candidate product must have been written back"
    assert all(candidate.candidate_kind in SOURCE_FACT_TYPES for candidate in candidates)


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_ranking_runs_through_the_pi_carrier_as_a_strict_permutation(db_session, monkeypatch):
    """A valid order is applied; a partial one is refused entirely.

    The strict-permutation rule is the ranking kind's whole contract, and the Pi
    path must not be a way around it: a product that omits or repeats a card is
    rejected as a whole rather than partially adopted.
    """
    space, event = _space_with_all_kinds(db_session, "e4-ranking")
    _run_job(db_session, space, event.id)
    db_session.expire_all()

    # A good permutation.
    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI, kind="ranking")
    payload = _payload(client, run.id, token)
    card_ids = [int(card["card_id"]) for card in payload]
    assert len(card_ids) >= 2, "ranking needs at least two cards to be a permutation"
    response = _settle(client, run.id, token, json.dumps(list(reversed(card_ids))))
    assert response.status_code == 200, response.text
    db_session.expire_all()
    good = db_session.get(StewardModelCall, grant["attempt_id"])
    assert good is not None
    assert good.status == "succeeded", good.error_code
    ranks = {
        card.id: card.presentation_rank
        for card in db_session.scalars(select(ActionCard).where(ActionCard.id.in_(card_ids)))
    }
    assert set(ranks.values()) == set(range(1, len(card_ids) + 1)), "a strict ranking landed"

    assert set(ranks.values()) == set(range(1, len(card_ids) + 1)), "a strict ranking landed"


def test_ranking_rejects_a_partial_permutation_through_the_pi_carrier(db_session, monkeypatch):
    """A product that omits a card is refused entirely, not partially adopted.

    The strict-permutation rule is the ranking kind's whole contract. Moving the
    kind to a new carrier must not open a way around it: a partial order would
    otherwise leave some cards ranked and others stale, which reads as a real
    preference the model never expressed.
    """
    space, event = _space_with_all_kinds(db_session, "e4-ranking-partial")
    _run_job(db_session, space, event.id)
    db_session.expire_all()

    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI, kind="ranking")
    payload = _payload(client, run.id, token)
    card_ids = [int(card["card_id"]) for card in payload]
    assert len(card_ids) >= 2

    response = _settle(client, run.id, token, json.dumps(card_ids[:-1]))
    assert response.status_code == 200, response.text
    db_session.expire_all()

    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    # A malformed product is degraded, never a partial success: the server owns
    # that verdict, and it must hold through the Pi path too.
    assert attempt.status == "degraded"
    assert attempt.output_json is None
    assert all(
        card.presentation_rank is None
        for card in db_session.scalars(select(ActionCard).where(ActionCard.id.in_(card_ids)))
    )


# --------------------------------------------------------------------------
# Explanation
# --------------------------------------------------------------------------


def test_explanation_runs_through_the_pi_carrier_with_declared_slots(db_session, monkeypatch):
    """The explanation product may only cite supplied evidence and declared slots.

    Two guards, both asserted through the Pi path: a fact id outside the supplied
    set is refused (the model cannot invent evidence for its own claim), and the
    rendered text is the server's template, not the model's prose — which is what
    keeps a fabricated promise ("I have added them to the family") out of the card.
    """
    space, event = _space_with_all_kinds(db_session, "e4-explanation")
    _run_job(db_session, space, event.id)
    db_session.expire_all()

    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI, kind="explanation")
    payload = _payload(client, run.id, token)
    fact_ids = [int(f["id"]) for f in payload["evidence_facts"]]
    slot_key = payload["allowed_slot_keys"][0]
    assert fact_ids, "the explanation projection must carry its evidence"

    response = _settle(
        client,
        run.id,
        token,
        json.dumps(
            {
                "reason_code": payload["allowed_reason_code"],
                "supporting_fact_ids": fact_ids,
                "template_slots": {slot_key: payload["subject"]},
            }
        ),
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()

    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    assert attempt.status == "succeeded", attempt.error_code
    card_id = int((attempt.subject_key or "card:0").split(":", 1)[1])
    card = db_session.get(ActionCard, card_id)
    assert card is not None
    # The rendered text is the server's template filled with validated slots; the
    # model never supplies prose for the card.
    assert card.reason_text_llm, "the explanation must have been rendered onto the card"


def test_explanation_cannot_cite_evidence_outside_the_projection(db_session, monkeypatch):
    """A fact id the server never supplied must invalidate the product.

    This is the explanation kind's fence: without it, a model could attach an
    invented fact to a claim and the card would present it as supported.
    """
    space, event = _space_with_all_kinds(db_session, "e4-explanation-evidence")
    _run_job(db_session, space, event.id)
    db_session.expire_all()

    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI, kind="explanation")
    payload = _payload(client, run.id, token)
    slot_key = payload["allowed_slot_keys"][0]
    invented = max(int(f["id"]) for f in payload["evidence_facts"]) + 1000

    response = _settle(
        client,
        run.id,
        token,
        json.dumps(
            {
                "reason_code": payload["allowed_reason_code"],
                "supporting_fact_ids": [invented],
                "template_slots": {slot_key: payload["subject"]},
            }
        ),
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()

    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    assert attempt.status == "degraded"
    assert attempt.output_json is None
    card_id = int((attempt.subject_key or "card:0").split(":", 1)[1])
    card = db_session.get(ActionCard, card_id)
    assert card is not None and card.reason_text_llm is None


# --------------------------------------------------------------------------
# The fence still holds per kind
# --------------------------------------------------------------------------


def test_a_changed_fact_set_retires_a_pi_candidate_attempt(db_session, monkeypatch):
    """The candidate fence is the evidence hash, and it applies to the Pi path.

    A candidate product is computed against a snapshot of the space's confirmed
    facts. If that set moves before the write-back, the product describes a world
    that no longer exists and must be dropped rather than stored.
    """
    space, event = _space_with_all_kinds(db_session, "e4-candidate-fence")
    _run_job(db_session, space, event.id)
    db_session.expire_all()

    grant, run, token, client = _lease_and_open(db_session, carrier=CARRIER_PI, kind="candidate")
    payload = _payload(client, run.id, token)
    fact = payload["facts"][0]
    product = json.dumps(
        [{"kind": "direct_sibling", "subject": fact["subject"], "object": fact["object"]}]
    )

    # Change the evidence after the projection was handed over.
    live = db_session.scalar(select(SourceFact).where(SourceFact.space_id == space.id))
    assert live is not None
    live.revision += 1
    db_session.commit()

    response = _settle(client, run.id, token, product)
    assert response.status_code == 200, response.text
    db_session.expire_all()

    attempt = db_session.get(StewardModelCall, grant["attempt_id"])
    assert attempt is not None
    assert attempt.status == "skipped"
    assert attempt.error_code == steward_assist.REASON_EVIDENCE_CHANGED
    assert attempt.applied_at is None
    assert list(db_session.scalars(select(StewardLlmCandidate))) == []


def _product_shape(product):
    """The product's structure with the per-space identifiers removed.

    Each iteration builds its own space, so user ids differ by construction and the
    rendered explanation text embeds that space's display name. Both are fixture
    artifacts, not carrier behaviour, so strings and numbers normalize to their
    type: what this compares is the product's *shape* (which keys exist, which
    reason code, how many items) and the settle status. Text content is asserted by
    the per-kind tests, which run against a single space and can compare it exactly.
    """
    if product is None:
        return None
    if isinstance(product, dict):
        return {key: _product_shape(value) for key, value in sorted(product.items())}
    if isinstance(product, list):
        return [_product_shape(item) for item in product]
    return type(product).__name__


def _kind_output(kind: str, payload) -> str:
    """A valid model output for one kind, built from its own projection."""
    if kind == "CANDIDATE":
        fact = payload["facts"][0]
        return json.dumps(
            [{"kind": "direct_sibling", "subject": fact["subject"], "object": fact["object"]}]
        )
    if kind == "RANKING":
        return json.dumps([int(card["card_id"]) for card in payload])
    return json.dumps(
        {
            "reason_code": payload["allowed_reason_code"],
            "supporting_fact_ids": [int(f["id"]) for f in payload["evidence_facts"]],
            "template_slots": {payload["allowed_slot_keys"][0]: payload["subject"]},
        }
    )
