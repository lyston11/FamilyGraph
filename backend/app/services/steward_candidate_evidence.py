"""Internal, deterministic support versions for stable Steward candidates.

The certificate proves complete common biological-parent pairs at a recorded
revision. It is neither a new SourceFact nor a public suggestion. Callers own
the write transaction and the assist/delivery lease and snapshot fences.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models.relationship_facts import SourceFact
from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.models.steward import (
    StewardAssistBatch,
    StewardCandidateEvidenceVersion,
    StewardJob,
    StewardLlmCandidate,
    StewardModelCall,
)
from app.services.steward_guard import candidate_digest
from app.utils.timeutil import utcnow

VALIDATION_CONTRACT_VERSION = "common-biological-parent-v1"
_FACT_KEYS = frozenset(
    {"id", "revision", "fact_type", "subject_user_id", "object_user_id", "space_id"}
)


def _sibling_endpoints(candidate: StewardLlmCandidate) -> tuple[int, int] | None:
    payload = candidate.payload_json
    if (
        candidate.candidate_kind != "direct_sibling"
        or not isinstance(payload, dict)
        or payload.get("kind") != "direct_sibling"
    ):
        return None
    subject, object_ = payload.get("subject_user_id"), payload.get("object_user_id")
    if (
        type(subject) is not int
        or type(object_) is not int
        or subject <= 0
        or object_ <= 0
        or subject == object_
    ):
        return None
    return subject, object_


def _pair_digests(endpoints: tuple[int, int]) -> set[str]:
    subject, object_ = endpoints
    return {
        candidate_digest("direct_sibling", subject, object_),
        candidate_digest("direct_sibling", object_, subject),
    }


def is_internal_candidate(db: Session, candidate: StewardLlmCandidate) -> bool:
    """Both public entrypoints share sticky, symmetric sibling isolation.

    The existing (space_id, candidate_digest) index bounds the reverse lookup
    to two identities, without merging/reordering either historical candidate.
    """
    if candidate.attribution_status == "versioned":
        return True
    endpoints = _sibling_endpoints(candidate)
    if endpoints is None:
        return False
    return (
        db.scalar(
            select(StewardLlmCandidate.id)
            .where(
                StewardLlmCandidate.space_id == candidate.space_id,
                StewardLlmCandidate.candidate_digest.in_(_pair_digests(endpoints)),
                StewardLlmCandidate.attribution_status == "versioned",
            )
            .limit(1)
        )
        is not None
    )


def _mark_internal(db: Session, candidate: StewardLlmCandidate) -> None:
    candidate.attribution_status = "versioned"
    endpoints = _sibling_endpoints(candidate)
    if endpoints is not None:
        for counterpart in db.scalars(
            select(StewardLlmCandidate).where(
                StewardLlmCandidate.space_id == candidate.space_id,
                StewardLlmCandidate.candidate_digest.in_(_pair_digests(endpoints)),
            )
        ):
            # Mode is not certification: a legacy reverse row keeps its own
            # payload, source job, status and history even before it has a version.
            counterpart.attribution_status = "versioned"


def _scoped_user_ids(db: Session, space: FamilySpace, needed: set[int]) -> set[int]:
    """Steward's active member/ref/owner scope, restricted to certificate nodes.

    This is the same space-internal rule as steward._space_visible_user_ids,
    not personal read authorization. Delivery never scans a whole-space graph.
    """
    members = select(SpaceMember.user_id).where(
        SpaceMember.space_id == space.id,
        SpaceMember.status == "active",
        SpaceMember.user_id.in_(needed),
    )
    refs = select(SpaceProfileRef.user_id).where(
        SpaceProfileRef.space_id == space.id,
        SpaceProfileRef.status == "active",
        SpaceProfileRef.user_id.in_(needed),
    )
    return set(db.scalars(members.union(refs))) | ({space.owner_id} & needed)


def _snapshot(fact: SourceFact) -> dict[str, Any]:
    return {
        "id": fact.id,
        "revision": fact.revision,
        "fact_type": fact.fact_type,
        "subject_user_id": fact.subject_user_id,
        "object_user_id": fact.object_user_id,
        "space_id": fact.space_id,
    }


def _complete_parents(facts: list[dict[str, Any]], endpoints: set[int]) -> set[int]:
    children: dict[int, set[int]] = {}
    for fact in facts:
        if (
            fact["fact_type"] == "biological_parent"
            and fact["subject_user_id"] not in endpoints
            and fact["object_user_id"] in endpoints
        ):
            children.setdefault(fact["subject_user_id"], set()).add(fact["object_user_id"])
    return {parent for parent, objects in children.items() if objects == endpoints}


def _support_for_candidate(db: Session, candidate: StewardLlmCandidate) -> list[dict[str, Any]]:
    endpoints = _sibling_endpoints(candidate)
    space = db.get(FamilySpace, candidate.space_id)
    if endpoints is None or space is None:
        return []
    facts = list(
        db.scalars(
            select(SourceFact)
            .where(
                SourceFact.fact_type == "biological_parent",
                SourceFact.state == "confirmed",
                SourceFact.object_user_id.in_(endpoints),
                (SourceFact.space_id == space.id) | SourceFact.space_id.is_(None),
            )
            .order_by(SourceFact.id)
        )
    )
    needed = set(endpoints) | {fact.subject_user_id for fact in facts}
    visible = _scoped_user_ids(db, space, needed)
    if not set(endpoints) <= visible:
        return []
    snapshots = [_snapshot(fact) for fact in facts if fact.subject_user_id in visible]
    parents = _complete_parents(snapshots, set(endpoints))
    return [fact for fact in snapshots if fact["subject_user_id"] in parents]


def _evidence_digest(facts: list[dict[str, Any]], contract: str) -> str:
    value = {"validation_contract_version": contract, "facts": facts}
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_for_candidate(
    db: Session,
    candidate: StewardLlmCandidate,
    *,
    batch: StewardAssistBatch,
    model_call: StewardModelCall,
    now: datetime,
) -> StewardCandidateEvidenceVersion | None:
    """Record support inside the already-fenced assist writeback transaction.

    The database unique key arbitrates repeated support across jobs/workers;
    conflict handling never overwrites the first source or an old attestation.
    """
    if (
        batch.space_id != candidate.space_id
        or model_call.space_id != batch.space_id
        or model_call.job_id != batch.job_id
        or model_call.batch_id != batch.id
        or model_call.assist_kind != "candidate"
        or model_call.status != "succeeded"
    ):
        raise ValueError("candidate evidence provenance mismatch")
    support = _support_for_candidate(db, candidate)
    if not support:
        if is_internal_candidate(db, candidate):
            _mark_internal(db, candidate)
        else:
            candidate.attribution_status = "unsupported"
        return None
    _mark_internal(db, candidate)
    digest = _evidence_digest(support, VALIDATION_CONTRACT_VERSION)
    db.flush()
    db.execute(
        insert(StewardCandidateEvidenceVersion)
        .values(
            candidate_id=candidate.id,
            space_id=candidate.space_id,
            validation_contract_version=VALIDATION_CONTRACT_VERSION,
            evidence_digest=digest,
            support_facts_json=support,
            source_job_id=batch.job_id,
            source_batch_id=batch.id,
            source_model_call_id=model_call.id,
            status="pending",
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["candidate_id", "evidence_digest"])
    )
    return db.scalar(
        select(StewardCandidateEvidenceVersion).where(
            StewardCandidateEvidenceVersion.candidate_id == candidate.id,
            StewardCandidateEvidenceVersion.evidence_digest == digest,
        )
    )


def _validate_saved_support(
    db: Session, candidate: StewardLlmCandidate, version: StewardCandidateEvidenceVersion
) -> str | None:
    if version.validation_contract_version != VALIDATION_CONTRACT_VERSION:
        return "unsupported_contract"
    endpoints = _sibling_endpoints(candidate)
    if endpoints is None or candidate.attribution_status != "versioned":
        return "candidate_structure_changed"
    support = version.support_facts_json
    if not isinstance(support, list) or not support:
        return "invalid_support_snapshot"
    for fact in support:
        if (
            not isinstance(fact, dict)
            or set(fact) != _FACT_KEYS
            or any(
                type(fact[key]) is not int or fact[key] <= 0
                for key in ("id", "revision", "subject_user_id", "object_user_id")
            )
            or (fact["space_id"] is not None and type(fact["space_id"]) is not int)
            or fact["space_id"] not in (None, version.space_id)
            or fact["fact_type"] != "biological_parent"
            or fact["subject_user_id"] in endpoints
            or fact["object_user_id"] not in endpoints
        ):
            return "invalid_support_snapshot"
    ids = [fact["id"] for fact in support]
    if (
        ids != sorted(set(ids))
        or _evidence_digest(support, version.validation_contract_version) != version.evidence_digest
    ):
        return "invalid_support_snapshot"
    parents = _complete_parents(support, set(endpoints))
    if not parents or any(fact["subject_user_id"] not in parents for fact in support):
        return "invalid_support_snapshot"
    # Read exactly the stored sources; new common parents belong to another
    # version and cannot silently replace this snapshot or rescue its revision.
    current = {
        fact.id: fact for fact in db.scalars(select(SourceFact).where(SourceFact.id.in_(ids)))
    }
    for saved in support:
        source = current.get(saved["id"])
        if source is None:
            return "source_missing"
        if source.state != "confirmed":
            return "source_not_confirmed"
        if source.revision != saved["revision"]:
            return "source_revision_changed"
        if source.space_id != saved["space_id"]:
            return "source_scope_changed"
        if _snapshot(source) != saved:
            return "source_structure_changed"
    space = db.get(FamilySpace, version.space_id)
    needed = set(endpoints) | parents
    if space is None or _scoped_user_ids(db, space, needed) != needed:
        return "source_out_of_scope"
    return None


def project_version(
    db: Session,
    *,
    candidate_id: int,
    version_id: int,
    job: StewardJob,
    now: datetime | None = None,
) -> bool:
    """Check only the captured pending version; return whether it became terminal.

    Delivery owns publication/input/lease fences and commits this result with
    the intent's done bit. Terminal rows are historical attestations, so neither
    source changes nor retries rewrite them or trigger public side effects.
    """
    version = db.scalar(
        select(StewardCandidateEvidenceVersion).where(
            StewardCandidateEvidenceVersion.id == version_id,
            StewardCandidateEvidenceVersion.candidate_id == candidate_id,
            StewardCandidateEvidenceVersion.space_id == job.space_id,
        )
    )
    if version is None or version.status != "pending":
        return False
    candidate = db.get(StewardLlmCandidate, candidate_id)
    reason = (
        "candidate_structure_changed"
        if candidate is None or candidate.space_id != job.space_id
        else _validate_saved_support(db, candidate, version)
    )
    version.status = "projected" if reason is None else "invalidated"
    version.projection_job_id = job.id
    version.projection_checked_at = now or utcnow()
    version.invalidation_reason = reason
    db.flush()
    return True
