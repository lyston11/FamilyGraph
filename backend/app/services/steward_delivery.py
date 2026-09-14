"""Publication-gated, per-object idempotent delivery.

Core prepares indexed intents; published generation status is their atomic
activation gate. Terminology prepares a bounded target batch outside the writer;
local effects and intent done share one short transaction. No HTTP runs here.
A poison object never rolls back another delivery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Exists

from app import config
from app.models.account import Account
from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember
from app.models.steward import (
    ActionCard,
    StewardCandidateEvidenceVersion,
    StewardDeliveryIntent,
    StewardFindingDelivery,
    StewardGeneration,
    StewardGenerationView,
    StewardInputRevision,
    StewardJob,
    StewardLlmCandidate,
    StewardPublication,
    StewardRetryBudget,
)
from app.models.steward_inferred import StewardInferredEdge
from app.services import (
    action_cards,
    steward_assist,
    steward_candidate_evidence,
    steward_inferred,
    steward_suggestions,
)
from app.services.recommendation_matrix import evaluate_recommendation
from app.utils.timeutil import utcnow

SUGGESTION_RECIPIENT_BATCH_SIZE = 8
_TERMINOLOGY_COMPLETE_KEY = "terminology:complete"
logger = logging.getLogger(__name__)


def valid_source(
    session: Session, intent: StewardDeliveryIntent, generation: StewardGeneration
) -> bool:
    """Only local terminology can continue across its own presentation writes."""
    if intent.kind == "terminology":
        from app.services.steward_terminology import valid_delivery_generation

        return valid_delivery_generation(session, generation)
    from app.services.steward_pipeline import valid_generation

    return valid_generation(session, generation)


def terminology_blocks_core(*, now: datetime) -> Exists:
    """Defer this space without leasing a coordinator or consuming a job attempt.

    A source/authorization/config change releases the gate immediately. The
    remaining obsolete intents can retire independently in bounded batches.
    """
    from app.services.steward_snapshot import SNAPSHOT_VERSION, config_fingerprint

    global_revision = aliased(StewardInputRevision)
    space_revision = aliased(StewardInputRevision)
    changed_delivery = aliased(StewardDeliveryIntent)
    return (
        select(StewardDeliveryIntent.id)
        .join(StewardGeneration, StewardGeneration.id == StewardDeliveryIntent.generation_id)
        .join(StewardPublication, StewardPublication.generation_id == StewardGeneration.id)
        .outerjoin(global_revision, global_revision.scope_id == 0)
        .outerjoin(space_revision, space_revision.scope_id == StewardGeneration.space_id)
        .where(
            StewardDeliveryIntent.space_id == StewardJob.space_id,
            StewardDeliveryIntent.kind == "terminology",
            StewardDeliveryIntent.status == "pending",
            StewardGeneration.status == "published",
            StewardGeneration.manifest_sealed.is_(True),
            StewardGeneration.valid_until > now,
            StewardGeneration.input_versions_json["version"].as_string() == SNAPSHOT_VERSION,
            StewardGeneration.input_versions_json["config"].as_string() == config_fingerprint(),
            StewardGeneration.input_versions_json["global"][0].as_integer()
            == func.coalesce(global_revision.structural, 0),
            StewardGeneration.input_versions_json["space"][0].as_integer()
            == func.coalesce(space_revision.structural, 0),
            or_(
                StewardGeneration.input_versions_json["global"][1].as_integer()
                != func.coalesce(global_revision.presentation, 0),
                StewardGeneration.input_versions_json["space"][1].as_integer()
                != func.coalesce(space_revision.presentation, 0),
            ),
            select(changed_delivery.id)
            .where(
                changed_delivery.generation_id == StewardGeneration.id,
                changed_delivery.kind == "terminology",
                changed_delivery.status == "done",
                changed_delivery.payload_json["changed"].as_boolean().is_(True),
            )
            .exists(),
        )
        .exists()
    )


def _terminology_already_delivered(session: Session, prior: StewardGeneration | None) -> bool:
    from app.services import steward_snapshot, steward_terminology

    if prior is None or not steward_terminology.valid_delivery_generation(session, prior):
        return False
    receipt = session.scalar(
        select(StewardDeliveryIntent.payload_json).where(
            StewardDeliveryIntent.generation_id == prior.id,
            StewardDeliveryIntent.intent_key == _TERMINOLOGY_COMPLETE_KEY,
            StewardDeliveryIntent.status == "done",
        )
    )
    if (
        receipt is None
        or not receipt.get("inputs_stable")
        or not steward_snapshot.versions_match(
            session, space_id=prior.space_id, expected=receipt.get("final_input_versions", {})
        )
    ):
        return False
    return (
        session.scalar(
            select(StewardDeliveryIntent.id)
            .where(
                StewardDeliveryIntent.generation_id == prior.id,
                StewardDeliveryIntent.kind == "terminology",
                StewardDeliveryIntent.status != "done",
            )
            .limit(1)
        )
        is None
    )


def _terminology_intents(
    session: Session, *, generation_id: int, prior: StewardGeneration | None
) -> list[dict[str, Any]]:
    from app.services import steward_terminology
    from app.services.steward_pipeline import valid_generation
    from app.services.steward_snapshot import canonical_hash

    # A constant receipt carries completion forward. Hot scans and the one
    # presentation-only successor must not enumerate every viewer/target again.
    items = (
        []
        if _terminology_already_delivered(session, prior)
        else steward_terminology.delivery_items_for_generation(session, generation_id=generation_id)
    )
    completed_keys = (
        set(
            session.scalars(
                select(StewardDeliveryIntent.intent_key).where(
                    StewardDeliveryIntent.generation_id == prior.id,
                    StewardDeliveryIntent.kind == "terminology",
                    StewardDeliveryIntent.status == "done",
                    StewardDeliveryIntent.payload_json["phase"].as_string() == "target",
                )
            )
        )
        if prior is not None and valid_generation(session, prior)
        else set()
    )
    intents: list[dict[str, Any]] = [
        {
            "key": (
                f"terminology:{item['viewer_account_id']}:"
                f"{item['root_user_id']}:"
                f"{canonical_hash(sorted(target['target_user_id'] for target in item['targets']))}"
            ),
            "kind": "terminology",
            "payload": {
                "phase": "target",
                **{k: v for k, v in item.items() if k != "generation_id"},
            },
        }
        for item in items
    ]
    intents = [intent for intent in intents if intent["key"] not in completed_keys]
    # Each refresh writes at most one viewer's event. All targets are terminal
    # before any refresh; leasing the successor waits for every refresh receipt.
    for account_id in sorted({intent["payload"]["viewer_account_id"] for intent in intents}):
        intents.append(
            {
                "key": f"terminology:refresh:{account_id}",
                "kind": "terminology",
                "payload": {"phase": "refresh", "viewer_account_id": account_id},
            }
        )
    intents.append(
        {"key": _TERMINOLOGY_COMPLETE_KEY, "kind": "terminology", "payload": {"phase": "complete"}}
    )
    return intents


def effect_fingerprint(
    generation: StewardGeneration, *, kind: str, key: str, payload: dict[str, Any]
) -> str:
    from app.services.steward_snapshot import canonical_hash, core_versions

    return canonical_hash(
        {
            "version": "steward-delivery-v1",
            "kind": kind,
            "key": key,
            "payload": payload,
            "inputs": core_versions(generation.input_versions_json),
            "valid_until": generation.valid_until.isoformat() if generation.valid_until else None,
        }
    )


def prepare_intents(
    session: Session,
    *,
    job: StewardJob,
    space: FamilySpace,
    visible: set[int],
    findings: list[dict[str, Any]],
    generation_id: int,
) -> list[dict[str, Any]]:
    from app.services import steward

    publication = session.get(StewardPublication, space.id)
    prior = (
        session.get(StewardGeneration, publication.generation_id)
        if publication is not None
        else None
    )
    prior_findings = (
        {
            row.intent_key: row.payload_json
            for row in session.scalars(
                select(StewardDeliveryIntent).where(
                    StewardDeliveryIntent.generation_id == prior.id,
                    StewardDeliveryIntent.kind == "finding",
                )
            )
        }
        if prior is not None
        else {}
    )
    legacy_signatures = (
        steward._prior_finding_signatures(session, space.id) if prior is None else set()
    )
    recipient_ids = list(
        session.scalars(
            select(Account.id)
            .join(SpaceMember, SpaceMember.user_id == Account.user_id)
            .where(SpaceMember.space_id == space.id, SpaceMember.status == "active")
            .order_by(Account.id)
        )
    )
    recipient_batches = [
        recipient_ids[offset : offset + SUGGESTION_RECIPIENT_BATCH_SIZE]
        for offset in range(0, len(recipient_ids), SUGGESTION_RECIPIENT_BATCH_SIZE)
    ] or [[]]
    intents: list[dict[str, Any]] = []
    for finding in {item["signature"]: item for item in findings}.values():
        signature = finding["signature"]
        previous = prior_findings.get(f"finding:{signature}", {})
        intents.append(
            {
                "key": f"finding:{signature}",
                "kind": "finding",
                "payload": {
                    "finding": finding,
                    "occurrence_generation_id": previous.get(
                        "occurrence_generation_id", generation_id
                    ),
                    "legacy_delivered": previous.get(
                        "legacy_delivered", signature in legacy_signatures
                    ),
                },
            }
        )
        # A finding can notify an entire family. Persist bounded recipient
        # batches in the read-side plan so no delivery writer fans out without
        # a cap. Every batch revalidates membership before applying effects.
        for batch, account_ids in enumerate(recipient_batches):
            intents.append(
                {
                    "key": f"suggestion:{signature}:{batch}",
                    "kind": "suggestion_finding",
                    "payload": {
                        "finding": finding,
                        "recipient_account_ids": account_ids,
                    },
                }
            )
    for card in action_cards.active_cards_in_space(session, space.id):
        intents.append(
            {
                "key": f"card:{card.id}",
                "kind": "card_review",
                "payload": {"card_id": card.id, "revision": card.revision},
            }
        )
    for fact in steward._applicable_confirmed_facts(session, space, visible):
        intents.append(
            {
                "key": f"recommend:{fact.id}",
                "kind": "recommend",
                "payload": {"fact_id": fact.id, "revision": fact.revision},
            }
        )
    for edge in steward_inferred.active_edges(session, space.id):
        intents.append(
            {
                "key": f"inferred:{edge.id}",
                "kind": "inferred_review",
                "payload": {"edge_id": edge.id, "revision": edge.revision},
            }
        )
    candidates = session.scalars(
        select(StewardLlmCandidate)
        .where(StewardLlmCandidate.space_id == space.id, StewardLlmCandidate.status == "proposed")
        .order_by(StewardLlmCandidate.id)
    )
    for candidate in candidates:
        if steward_candidate_evidence.is_internal_candidate(session, candidate):
            continue
        intents.append(
            {
                "key": f"candidate:{candidate.id}",
                "kind": "candidate",
                "payload": {"candidate_id": candidate.id},
            }
        )
    # Capture IDs in the read-side plan, including dismissed candidates. A
    # version created after planning waits for a later generation; one intent
    # can never sweep up every currently pending version of the candidate.
    versions = session.execute(
        select(StewardCandidateEvidenceVersion.id, StewardCandidateEvidenceVersion.candidate_id)
        .where(
            StewardCandidateEvidenceVersion.space_id == space.id,
            StewardCandidateEvidenceVersion.status == "pending",
        )
        .order_by(StewardCandidateEvidenceVersion.id)
    )
    for version_id, candidate_id in versions:
        intents.append(
            {
                "key": f"candidate:evidence:{version_id}",
                "kind": "candidate",
                "payload": {"candidate_id": candidate_id, "evidence_version_id": version_id},
            }
        )
    intents.extend(_terminology_intents(session, generation_id=generation_id, prior=prior))
    # Only registration occurs here; the existing assist machine owns attempts,
    # reserved/in_flight/unknown and conservative billing. Never resend unknown.
    intents.append({"key": "assist", "kind": "assist", "payload": {}})
    if steward_inferred.effective_enabled(session, space.id):
        for account_id in session.scalars(
            select(StewardGenerationView.viewer_account_id).where(
                StewardGenerationView.generation_id == generation_id,
            )
        ):
            intents.append(
                {
                    "key": f"overlay:{account_id}",
                    "kind": "inferred_overlay",
                    "payload": {"account_id": account_id},
                }
            )
    generation = session.get(StewardGeneration, generation_id)
    assert generation is not None
    for intent in intents:
        intent["effect_fingerprint"] = effect_fingerprint(
            generation, kind=intent["kind"], key=intent["key"], payload=intent["payload"]
        )
    return intents


def _card_review(
    session: Session, *, space: FamilySpace, payload: dict[str, Any], now: datetime
) -> dict[str, int]:
    from app.services import steward

    card = session.get(ActionCard, int(payload["card_id"]))
    if (
        card is None
        or card.space_id != space.id
        or card.revision != payload["revision"]
        or card.state not in ("pending", "viewed", "accepted")
    ):
        return {}
    if card.expires_at is not None and card.expires_at < now:
        action_cards.transition_card(
            session, card, action_cards.ACTION_EXPIRE, expected_revision=card.revision, now=now
        )
        return {"cards_expired": 1}
    primary_id = card.evidence_json.get("primary_fact_id")
    fact = session.get(SourceFact, primary_id) if isinstance(primary_id, int) else None
    if fact is None or fact.state != "confirmed":
        action_cards.supersede_card(session, card, reason="evidence_invalidated", now=now)
        return {"cards_superseded": 1}
    inputs = steward._pair_inputs(session, space, fact)
    outcome = evaluate_recommendation(inputs)
    wanted = {steward._ACTION_TO_KIND[action] for action in outcome.actions}
    if not outcome.eligible or card.kind not in wanted:
        action_cards.supersede_card(session, card, reason="eligibility_lost", now=now)
        return {"cards_superseded": 1}
    if (
        action_cards.compute_evidence_hash(steward._evidence_json(fact, inputs))
        != card.evidence_hash
    ):
        for action in outcome.actions:
            if steward._ACTION_TO_KIND[action] == card.kind:
                steward._materialize_action(session, space, fact, action, inputs, now=now)
    return {}


def _apply(
    session: Session,
    intent: StewardDeliveryIntent,
    job: StewardJob,
    space: FamilySpace,
    *,
    prepared_registration: dict[str, Any] | None = None,
) -> dict[str, int]:
    from app.services import steward

    payload = intent.payload_json
    now = utcnow()
    if intent.kind == "candidate" and "evidence_version_id" in payload:
        # Internal evidence never loads the whole-space public projection
        # context and never calls suggestion/inferred upserts (even notify=False
        # would create recipients). The saved support owns its bounded recheck.
        return {
            "candidate_evidence_checked": int(
                steward_candidate_evidence.project_version(
                    session,
                    candidate_id=int(payload["candidate_id"]),
                    version_id=int(payload["evidence_version_id"]),
                    job=job,
                    now=now,
                )
            )
        }
    if intent.kind == "finding":
        signature = str(payload["finding"]["signature"])
        occurrence = int(payload["occurrence_generation_id"])
        if session.get(StewardFindingDelivery, (space.id, signature, occurrence)) is not None:
            return {"findings_emitted": 0}
        session.add(
            StewardFindingDelivery(
                space_id=space.id,
                signature=signature,
                occurrence_generation_id=occurrence,
                delivered_at=now,
            )
        )
        emitted = steward._emit_new_findings(
            session,
            job,
            [payload["finding"]],
            {signature} if payload.get("legacy_delivered") else set(),
            now=now,
        )
        return {"findings_emitted": emitted}
    if intent.kind == "card_review":
        return _card_review(session, space=space, payload=payload, now=now)
    if intent.kind == "recommend":
        fact = session.get(SourceFact, int(payload["fact_id"]))
        if (
            fact is None
            or fact.state != "confirmed"
            or fact.revision != payload["revision"]
            or fact.space_id not in (None, space.id)
        ):
            return {}
        inputs = steward._pair_inputs(session, space, fact)
        outcome = evaluate_recommendation(inputs)
        created = (
            sum(
                int(steward._materialize_action(session, space, fact, action, inputs, now=now))
                for action in outcome.actions
            )
            if outcome.eligible
            else 0
        )
        return {"cards_created": created}
    if intent.kind == "inferred_review":
        edge = session.get(StewardInferredEdge, int(payload["edge_id"]))
        if edge is None or edge.revision != payload["revision"]:
            return {}
        return {
            "inferred_superseded": steward_inferred.supersede_evidence_changed(
                session, space.id, now=now, edge_ids=[edge.id]
            )
        }
    visible = steward._space_visible_user_ids(session, space)
    facts = steward._applicable_confirmed_facts(session, space, visible)
    if intent.kind == "suggestion_finding":
        return {
            "suggestions_projected": steward_suggestions.project_for_job(
                session,
                job,
                findings=[payload["finding"]],
                facts=facts,
                now=now,
                candidate_ids=[],
                recipient_account_ids=payload["recipient_account_ids"],
            )
        }
    if intent.kind == "candidate":
        candidate_id = int(payload["candidate_id"])
        return {
            "suggestions_projected": steward_suggestions.project_for_job(
                session, job, findings=[], facts=facts, now=now, candidate_ids=[candidate_id]
            ),
            "inferred_projected": steward_inferred.project_for_job(
                session, job, facts=facts, visible=visible, now=now, candidate_ids=[candidate_id]
            ),
        }
    if intent.kind == "assist":
        cards = list(
            session.scalars(
                select(ActionCard)
                .where(ActionCard.space_id == space.id, ActionCard.state.in_(("pending", "viewed")))
                .order_by(ActionCard.id)
                .limit(config.STEWARD_ASSIST_MAX_CARDS_PER_JOB)
            )
        )
        steward_assist.register_batch_for_job(
            session,
            job=job,
            facts_brief=steward._confirmed_facts_brief(session, space, visible),
            visible=visible,
            cards=cards,
            prepared=prepared_registration,
        )
        return {}
    raise ValueError("unknown delivery intent")


def _record_terminology_progress(
    session: Session,
    generation: StewardGeneration,
    *,
    before_versions: dict[str, Any],
) -> None:
    from app.services.steward_snapshot import core_versions, input_versions

    state = dict(generation.stats_json.get("terminology_delivery", {}))
    expected = state.get("next_versions", generation.input_versions_json)
    # Only a chain of our own, CAS-checked writes can certify the final inputs.
    # A user changing another presentation input midway requires another scan.
    state["source_changed"] = bool(state.get("source_changed")) or (
        core_versions(expected) != core_versions(before_versions)
    )
    state["next_versions"] = input_versions(session, generation.space_id)
    generation.stats_json = {**generation.stats_json, "terminology_delivery": state}


def _apply_terminology(
    session: Session,
    intent: StewardDeliveryIntent,
    generation: StewardGeneration,
    job: StewardJob,
    prepared: dict[str, Any] | None,
) -> dict[str, int]:
    from app.services import steward_snapshot, steward_terminology

    payload = intent.payload_json
    phase = payload["phase"]
    if phase == "target":
        before = steward_snapshot.input_versions(session, intent.space_id)
        result = (
            steward_terminology.apply_delivery_item(session, job=job, prepared=prepared)
            if prepared is not None
            else {"projections": 0, "suggestions": 0, "changed": False}
        )
        session.flush()
        _record_terminology_progress(session, generation, before_versions=before)
        intent.payload_json = {**payload, "changed": bool(result["changed"])}
        if result["changed"]:
            # An operator can retry a failed target after the first completion.
            # Re-arm at most its viewer refresh and the constant receipt; the
            # already registered assist attempt/unknown state is never reset.
            for barrier in session.scalars(
                select(StewardDeliveryIntent).where(
                    StewardDeliveryIntent.generation_id == generation.id,
                    StewardDeliveryIntent.intent_key.in_(
                        (
                            f"terminology:refresh:{payload['viewer_account_id']}",
                            _TERMINOLOGY_COMPLETE_KEY,
                        )
                    ),
                    StewardDeliveryIntent.status == "done",
                )
            ):
                barrier.status, barrier.updated_at, barrier.available_at = "pending", utcnow(), None
        return {
            "terminology_projections": int(result["projections"]),
            "terminology_suggestions": int(result["suggestions"]),
        }
    if phase == "refresh":
        account_id = int(payload["viewer_account_id"])
        changed = session.scalar(
            select(StewardDeliveryIntent.id)
            .where(
                StewardDeliveryIntent.generation_id == generation.id,
                StewardDeliveryIntent.kind == "terminology",
                StewardDeliveryIntent.status == "done",
                StewardDeliveryIntent.payload_json["phase"].as_string() == "target",
                StewardDeliveryIntent.payload_json["viewer_account_id"].as_integer() == account_id,
                StewardDeliveryIntent.payload_json["changed"].as_boolean().is_(True),
            )
            .limit(1)
        )
        if changed is not None:
            steward_terminology.request_projection_refresh(
                session, space_id=intent.space_id, viewer_account_ids={account_id}
            )
        return {}
    if phase == "complete":
        final_versions = steward_snapshot.input_versions(session, intent.space_id)
        _record_terminology_progress(session, generation, before_versions=final_versions)
        intent.payload_json = {
            **payload,
            "final_input_versions": final_versions,
            "inputs_stable": not generation.stats_json["terminology_delivery"]["source_changed"],
        }
        return {}
    raise ValueError("unknown terminology delivery phase")


def _prerequisites_ready() -> ColumnElement[bool]:
    sibling = aliased(StewardDeliveryIntent)
    same_generation = (
        sibling.generation_id == StewardDeliveryIntent.generation_id,
        sibling.kind == "terminology",
    )
    pending_targets = (
        select(sibling.id)
        .where(
            *same_generation,
            sibling.payload_json["phase"].as_string() == "target",
            sibling.status == "pending",
        )
        .exists()
    )
    pending_before_completion = (
        select(sibling.id)
        .where(
            *same_generation,
            sibling.intent_key != _TERMINOLOGY_COMPLETE_KEY,
            or_(
                sibling.status == "pending",
                and_(
                    sibling.status == "failed",
                    sibling.payload_json["phase"].as_string() == "refresh",
                ),
            ),
        )
        .exists()
    )
    pending_before_assist = (
        select(sibling.id)
        .where(
            *same_generation,
            or_(
                sibling.status == "pending",
                and_(
                    sibling.status == "failed",
                    sibling.payload_json["phase"].as_string().in_(("refresh", "complete")),
                ),
            ),
        )
        .exists()
    )
    return and_(
        or_(StewardDeliveryIntent.kind != "assist", ~pending_before_assist),
        or_(
            StewardDeliveryIntent.kind != "terminology",
            StewardDeliveryIntent.payload_json["phase"].as_string() != "refresh",
            ~pending_targets,
        ),
        or_(
            StewardDeliveryIntent.kind != "terminology",
            StewardDeliveryIntent.payload_json["phase"].as_string() != "complete",
            ~pending_before_completion,
        ),
    )


@dataclass(frozen=True)
class _Claim:
    intent_id: int
    owner: str
    attempt: int
    kind: str = ""
    generation_id: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    space_id: int = 0


def _budget(
    session: Session, intent: StewardDeliveryIntent, generation: StewardGeneration
) -> StewardRetryBudget:
    if intent.effect_fingerprint is None:
        intent.effect_fingerprint = effect_fingerprint(
            generation, kind=intent.kind, key=intent.intent_key, payload=intent.payload_json
        )
    budget = session.scalar(
        select(StewardRetryBudget).where(
            StewardRetryBudget.space_id == intent.space_id,
            StewardRetryBudget.fingerprint == intent.effect_fingerprint,
            StewardRetryBudget.scope == "delivery",
        )
    )
    if budget is None:
        budget = StewardRetryBudget(
            space_id=intent.space_id,
            fingerprint=intent.effect_fingerprint,
            scope="delivery",
            attempts=0,
            max_attempts=config.STEWARD_STAGE_MAX_ATTEMPTS,
            exhausted=False,
            updated_at=utcnow(),
        )
        session.add(budget)
    return budget


def _release_success(
    session: Session, intent: StewardDeliveryIntent, generation: StewardGeneration
) -> None:
    budget = _budget(session, intent, generation)
    budget.attempts = max(0, budget.attempts - 1)
    budget.exhausted = budget.attempts >= budget.max_attempts
    budget.retry_after, budget.updated_at = None, utcnow()
    intent.lease_owner, intent.lease_until = None, None


def _claim_due(
    bind: Engine | Connection, *, owner: str, generation_id: int | None
) -> _Claim | Literal["delivery_failed", "delivery_superseded", "deferred"] | None:
    from app.services.steward_pipeline import write_transaction

    with write_transaction(bind) as session:
        now = utcnow()
        query = (
            select(StewardDeliveryIntent)
            .join(StewardGeneration)
            .where(
                StewardGeneration.status == "published",
                StewardDeliveryIntent.status == "pending",
                StewardDeliveryIntent.kind != "inferred_overlay",
                _prerequisites_ready(),
                (StewardDeliveryIntent.available_at.is_(None))
                | (StewardDeliveryIntent.available_at <= now),
                (StewardDeliveryIntent.lease_until.is_(None))
                | (StewardDeliveryIntent.lease_until <= now),
            )
            .order_by(StewardDeliveryIntent.id)
            .limit(1)
        )
        if generation_id is not None:
            query = query.where(StewardDeliveryIntent.generation_id == generation_id)
        intent = session.scalar(query)
        if intent is None:
            return None
        generation = session.get(StewardGeneration, intent.generation_id)
        assert generation is not None
        job = session.get(StewardJob, generation.job_id) if generation.job_id is not None else None
        if (
            job is None
            or job.status != "succeeded"
            or not valid_source(session, intent, generation)
        ):
            intent.status, intent.updated_at = "superseded", now
            intent.lease_owner, intent.lease_until = None, None
            return "delivery_superseded"
        budget = _budget(session, intent, generation)
        # A newer generation may carry the same effect while its predecessor
        # is in flight. Keep one active responsibility for that effect.
        other_lease = session.scalar(
            select(StewardDeliveryIntent.lease_until)
            .where(
                StewardDeliveryIntent.space_id == intent.space_id,
                StewardDeliveryIntent.effect_fingerprint == intent.effect_fingerprint,
                StewardDeliveryIntent.id != intent.id,
                StewardDeliveryIntent.status == "pending",
                StewardDeliveryIntent.lease_until > now,
            )
            .limit(1)
        )
        if other_lease is not None:
            intent.available_at, intent.updated_at = other_lease, now
            return "deferred"
        if budget.exhausted or budget.attempts >= budget.max_attempts:
            budget.exhausted, budget.updated_at = True, now
            intent.status, intent.error_code = "failed", "retry_budget_exhausted"
            intent.lease_owner, intent.lease_until = None, None
            intent.updated_at = now
            return "delivery_failed"
        if budget.retry_after is not None and budget.retry_after > now:
            intent.available_at, intent.updated_at = budget.retry_after, now
            return "deferred"
        # Persist before doing any work. A process crash consumes one finite
        # opportunity; neither a scan nor a new generation clears it.
        budget.attempts += 1
        budget.updated_at = now
        intent.attempt += 1
        intent.lease_owner = owner
        intent.lease_until = now + timedelta(seconds=config.STEWARD_LEASE_TTL_SECONDS)
        intent.updated_at = now
        return _Claim(
            intent.id,
            owner,
            intent.attempt,
            intent.kind,
            generation.id,
            dict(intent.payload_json),
            intent.space_id,
        )


def _matches_claim(intent: StewardDeliveryIntent | None, claim: _Claim) -> bool:
    return (
        intent is not None
        and intent.status == "pending"
        and intent.lease_owner == claim.owner
        and intent.attempt == claim.attempt
        and intent.lease_until is not None
        and intent.lease_until > utcnow()
    )


def _record_failure(bind: Engine | Connection, claim: _Claim) -> None:
    from app.services.steward_pipeline import write_transaction

    with write_transaction(bind) as session:
        intent = session.get(StewardDeliveryIntent, claim.intent_id)
        if not _matches_claim(intent, claim):
            return
        assert intent is not None
        generation = session.get(StewardGeneration, intent.generation_id)
        assert generation is not None
        if generation.status != "published" or not valid_source(session, intent, generation):
            intent.status, intent.updated_at = "superseded", utcnow()
            _release_success(session, intent, generation)
            return
        budget = _budget(session, intent, generation)
        budget.exhausted = budget.attempts >= budget.max_attempts
        budget.retry_after = utcnow() + timedelta(
            seconds=config.STEWARD_RETRY_BACKOFF_FIRST_SECONDS * max(1, budget.attempts)
        )
        budget.updated_at = utcnow()
        intent.status = "failed" if budget.exhausted else "pending"
        intent.error_code = "delivery_failed"
        intent.available_at, intent.updated_at = budget.retry_after, utcnow()
        intent.lease_owner, intent.lease_until = None, None


def _defer_changed_snapshot(bind: Engine | Connection, claim: _Claim) -> None:
    from app.services.steward_pipeline import write_transaction

    with write_transaction(bind) as session:
        intent = session.get(StewardDeliveryIntent, claim.intent_id)
        if not _matches_claim(intent, claim):
            return
        assert intent is not None
        generation = session.get(StewardGeneration, intent.generation_id)
        assert generation is not None
        if generation.status != "published" or not valid_source(session, intent, generation):
            intent.status = "superseded"
        intent.updated_at, intent.available_at = utcnow(), None
        _release_success(session, intent, generation)


def drain(
    *, bind: Engine | Connection, limit: int | None = None, generation_id: int | None = None
) -> dict[str, int]:
    from app.services import steward_terminology
    from app.services.steward_pipeline import write_transaction
    from app.services.steward_snapshot import SnapshotChanged

    counters: dict[str, int] = {"delivery_done": 0, "delivery_failed": 0, "delivery_superseded": 0}
    cap = limit if limit is not None else config.STEWARD_DELIVERY_PER_TICK
    owner = f"delivery:{uuid4().hex}"
    for _ in range(cap):
        claim = _claim_due(bind, owner=owner, generation_id=generation_id)
        if claim is None:
            break
        if isinstance(claim, str):
            if claim in counters:
                counters[claim] += 1
            continue
        try:
            if claim.kind == "terminology" and claim.payload.get("phase") == "target":
                prepared = steward_terminology.prepare_delivery_item(
                    bind, item={**claim.payload, "generation_id": claim.generation_id}
                )
            elif claim.kind == "assist":
                prepared = steward_assist.prepare_registration(bind, space_id=claim.space_id)
            else:
                prepared = None
            with write_transaction(bind) as session:
                intent = session.get(StewardDeliveryIntent, claim.intent_id)
                if not _matches_claim(intent, claim):
                    continue
                assert intent is not None
                generation = session.get(StewardGeneration, intent.generation_id)
                assert generation is not None
                job = (
                    session.get(StewardJob, generation.job_id)
                    if generation.job_id is not None
                    else None
                )
                space = session.get(FamilySpace, intent.space_id)
                if (
                    generation.status != "published"
                    or job is None
                    or job.status != "succeeded"
                    or space is None
                    or not valid_source(session, intent, generation)
                ):
                    intent.status, intent.updated_at = "superseded", utcnow()
                    _release_success(session, intent, generation)
                    counters["delivery_superseded"] += 1
                    continue
                # A manual retry can reopen a prerequisite after we claimed.
                if (
                    session.scalar(
                        select(StewardDeliveryIntent.id).where(
                            StewardDeliveryIntent.id == intent.id, _prerequisites_ready()
                        )
                    )
                    is None
                ):
                    _release_success(session, intent, generation)
                    continue
                if intent.kind == "terminology":
                    applied = _apply_terminology(session, intent, generation, job, prepared)
                elif intent.kind == "assist":
                    applied = _apply(session, intent, job, space, prepared_registration=prepared)
                else:
                    applied = _apply(session, intent, job, space)
                intent.status, intent.updated_at, intent.error_code = "done", utcnow(), None
                _release_success(session, intent, generation)
            for key, value in applied.items():
                counters[key] = counters.get(key, 0) + value
            counters["delivery_done"] += 1
            if claim.kind == "terminology" and prepared is not None:
                # Cache confirmation is advisory and follows the successful
                # commit, so a rolled-back revision can never certify a hit.
                try:
                    steward_terminology.confirm_delivery_item(bind, prepared=prepared)
                except Exception as exc:
                    logger.warning(
                        "steward terminology cache confirmation deferred (error=%s)",
                        type(exc).__name__,
                    )
        except SnapshotChanged:
            # A current presentation input raced the detached preparation.
            # It is fresh work, not a failed deterministic algorithm attempt.
            _defer_changed_snapshot(bind, claim)
        except Exception:
            # The local effect and done bit rolled back together. The durable
            # claim remains until this exact owner/attempt settles or expires.
            _record_failure(bind, claim)
            counters["delivery_failed"] += 1
    return counters


def backlog(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(StewardDeliveryIntent.status, func.count())
        .join(StewardGeneration, StewardGeneration.id == StewardDeliveryIntent.generation_id)
        .where(
            StewardGeneration.status == "published",
            StewardDeliveryIntent.status.in_(("pending", "failed")),
        )
        .group_by(StewardDeliveryIntent.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def retry_intent(
    session: Session,
    *,
    space_id: int,
    intent_id: int,
    expected_attempt: int,
    expected_policy_version: str,
) -> dict[str, Any]:
    """Grant one delivery opportunity inside the caller's short writer.

    expected_attempt is the replay/CAS token: a duplicate queued request adds
    nothing, and a late replay after another attempt cannot create a new grant.
    This only queues a local intent; assist HTTP/unknown state is never reset.
    """
    from app.errors import STEWARD_POLICY_CONFLICT, STEWARD_RERUN_TOO_FREQUENT, raise_api_error
    from app.services import steward, steward_overlay, steward_snapshot

    intent = session.get(StewardDeliveryIntent, intent_id, populate_existing=True)
    if intent is None or intent.space_id != space_id:
        raise_api_error(404, "STEWARD_DELIVERY_NOT_FOUND", "交付待办不存在")
    if expected_policy_version != steward.POLICY_VERSION:
        raise_api_error(409, STEWARD_POLICY_CONFLICT, "策略版本已改变，请重新读取")
    if intent.attempt != expected_attempt:
        raise_api_error(409, "STEWARD_DELIVERY_STALE", "交付已推进，请重新读取")
    generation = session.get(StewardGeneration, intent.generation_id)
    if (
        generation is None
        or generation.status != "published"
        or not valid_source(session, intent, generation)
        or intent.status == "superseded"
    ):
        raise_api_error(409, "STEWARD_DELIVERY_STALE", "交付依据已失效")
    if (
        intent.effect_fingerprint is not None
        and session.scalar(
            select(StewardDeliveryIntent.id)
            .join(StewardGeneration)
            .where(
                StewardDeliveryIntent.space_id == space_id,
                StewardDeliveryIntent.effect_fingerprint == intent.effect_fingerprint,
                StewardDeliveryIntent.id > intent.id,
                StewardDeliveryIntent.status.in_(("pending", "failed", "done")),
                StewardGeneration.status == "published",
            )
            .limit(1)
        )
        is not None
    ):
        raise_api_error(409, "STEWARD_DELIVERY_STALE", "交付已由后继待办接替，请重新读取")
    result = {
        "intent_id": intent.id,
        "status": intent.status,
        "attempt": intent.attempt,
        "coalesced": True,
    }
    if intent.status in ("pending", "done"):
        return result
    now = utcnow()
    if intent.kind == "inferred_overlay":
        publication = session.get(StewardPublication, space_id)
        if publication is None or publication.generation_id != generation.id:
            raise_api_error(409, "STEWARD_DELIVERY_STALE", "推测视图已由新发布代次接替")
        fingerprint = steward_overlay._fingerprint(
            steward_snapshot.input_versions(session, space_id)
        )
        scope = f"overlay:{int(intent.payload_json['account_id'])}"
        budget = session.scalar(
            select(StewardRetryBudget).where(
                StewardRetryBudget.space_id == space_id,
                StewardRetryBudget.fingerprint == fingerprint,
                StewardRetryBudget.scope == scope,
            )
        )
        if budget is None:
            budget = StewardRetryBudget(
                space_id=space_id,
                fingerprint=fingerprint,
                scope=scope,
                attempts=0,
                max_attempts=0,
                exhausted=True,
                updated_at=now,
            )
            session.add(budget)
    else:
        budget = _budget(session, intent, generation)
    if (
        budget.manual_retry_at is not None
        and budget.manual_retry_at + timedelta(seconds=config.STEWARD_RERUN_COOLDOWN_SECONDS) > now
    ):
        raise_api_error(429, STEWARD_RERUN_TOO_FREQUENT, "该交付重试过于频繁，请稍后再试")
    budget.max_attempts = budget.attempts + 1
    budget.exhausted, budget.retry_after = False, None
    budget.manual_grants = (budget.manual_grants or 0) + 1
    budget.manual_retry_at, budget.updated_at = now, now
    intent.status, intent.error_code, intent.available_at = "pending", None, now
    intent.lease_owner, intent.lease_until = None, None
    intent.updated_at = now
    return {**result, "status": "pending", "coalesced": False}
