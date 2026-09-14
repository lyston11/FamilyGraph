"""Independent, bounded inferred projection after confirmed publication.

Each viewer has a durable delivery intent and its own short lease. Augmented
graph search uses the same fair CPU slices as core work, outside transactions.
Its failure never invalidates confirmed progress or advances a core cursor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import exists, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, aliased

from app import config
from app.models.account import Account
from app.models.steward import (
    StewardDeliveryIntent,
    StewardGeneration,
    StewardGenerationView,
    StewardInferredOverlay,
    StewardJob,
    StewardPublication,
    StewardRetryBudget,
)
from app.models.steward_inferred import StewardInferredEdge
from app.models.user import User
from app.services import kinship_presentation, steward_inferred, steward_runtime, steward_snapshot
from app.services.relationship_graph import ExtraEdge
from app.services.relationship_resolver import concept_code_for_path, start_search, steps_to_json
from app.services.steward_pipeline import valid_generation, write_transaction
from app.services.steward_snapshot import SnapshotChanged, ViewerInput, canonical_hash
from app.services.terms import VariantContext, resolve_term_from_snapshot
from app.utils.timeutil import utcnow


@dataclass(frozen=True)
class OverlayBinding:
    intent_id: int
    generation_id: int
    space_id: int
    account_id: int
    owner: str
    attempt: int


def _fingerprint(versions: dict[str, Any]) -> str:
    return canonical_hash(
        {
            "global": [versions["global"][0], versions["global"][2]],
            "space": [versions["space"][0], versions["space"][2]],
            "config": versions["config"],
        }
    )


def active_leases(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count(StewardDeliveryIntent.id)).where(
                StewardDeliveryIntent.kind == "inferred_overlay",
                StewardDeliveryIntent.status == "pending",
                StewardDeliveryIntent.lease_until > utcnow(),
            )
        )
        or 0
    )


def claim_due(
    bind: Engine | Connection,
    *,
    owner: str,
    space_id: int | None = None,
) -> OverlayBinding | None:
    """Claim one optional viewer; crashes consume a persistent input budget."""
    dependency = aliased(StewardDeliveryIntent)
    for _ in range(16):
        with write_transaction(bind) as session:
            now = utcnow()
            core = int(
                session.scalar(
                    select(func.count(StewardJob.id)).where(
                        StewardJob.status.in_(("leased", "running")),
                        StewardJob.lease_expires_at > now,
                    )
                )
                or 0
            )
            if core + active_leases(session) >= config.STEWARD_MAX_CONCURRENT_JOBS:
                return None
            query = (
                select(StewardDeliveryIntent)
                .join(StewardGeneration)
                .where(
                    StewardGeneration.status == "published",
                    StewardDeliveryIntent.kind == "inferred_overlay",
                    StewardDeliveryIntent.status == "pending",
                    (StewardDeliveryIntent.available_at.is_(None))
                    | (StewardDeliveryIntent.available_at <= now),
                    (StewardDeliveryIntent.lease_until.is_(None))
                    | (StewardDeliveryIntent.lease_until <= now),
                    ~exists(
                        select(dependency.id).where(
                            dependency.generation_id == StewardDeliveryIntent.generation_id,
                            dependency.kind.in_(("candidate", "inferred_review")),
                            dependency.status == "pending",
                        )
                    ),
                )
                .order_by(StewardDeliveryIntent.id)
                .limit(1)
            )
            if space_id is not None:
                query = query.where(StewardDeliveryIntent.space_id == space_id)
            intent = session.scalar(query)
            if intent is None:
                return None
            generation = session.get(StewardGeneration, intent.generation_id)
            publication = session.get(StewardPublication, intent.space_id)
            if (
                generation is None
                or publication is None
                or publication.generation_id != generation.id
                or not valid_generation(session, generation)
            ):
                intent.status, intent.updated_at = "superseded", now
                continue
            if not steward_inferred.effective_enabled(session, intent.space_id):
                intent.status, intent.updated_at = "done", now
                continue
            account_id = int(intent.payload_json["account_id"])
            versions = steward_snapshot.input_versions(session, intent.space_id)
            cached = session.get(StewardInferredOverlay, (intent.space_id, account_id))
            if (
                cached is not None
                and cached.input_versions_json == versions
                and cached.valid_until > now
            ):
                intent.status, intent.updated_at = "done", now
                continue
            fingerprint = _fingerprint(versions)
            budget = session.scalar(
                select(StewardRetryBudget).where(
                    StewardRetryBudget.space_id == intent.space_id,
                    StewardRetryBudget.fingerprint == fingerprint,
                    StewardRetryBudget.scope == f"overlay:{account_id}",
                )
            )
            if budget is None:
                budget = StewardRetryBudget(
                    space_id=intent.space_id,
                    fingerprint=fingerprint,
                    scope=f"overlay:{account_id}",
                    attempts=0,
                    max_attempts=config.STEWARD_STAGE_MAX_ATTEMPTS,
                    exhausted=False,
                    updated_at=now,
                )
                session.add(budget)
            if budget.exhausted or budget.attempts >= budget.max_attempts:
                intent.status, intent.error_code = "failed", "retry_budget_exhausted"
                intent.updated_at = now
                continue
            budget.attempts += 1
            budget.updated_at = now
            intent.attempt += 1
            intent.lease_owner, intent.lease_until = (
                owner,
                now + timedelta(seconds=config.STEWARD_LEASE_TTL_SECONDS),
            )
            intent.payload_json = {**intent.payload_json, "input_versions": versions}
            intent.updated_at = now
            return OverlayBinding(
                intent.id, generation.id, intent.space_id, account_id, owner, intent.attempt
            )
    return None


def _require(
    session: Session, binding: OverlayBinding
) -> tuple[StewardDeliveryIntent, StewardGenerationView]:
    intent = session.get(StewardDeliveryIntent, binding.intent_id, populate_existing=True)
    now = utcnow()
    if (
        intent is None
        or intent.status != "pending"
        or intent.lease_owner != binding.owner
        or intent.attempt != binding.attempt
        or intent.lease_until is None
        or intent.lease_until <= now
    ):
        raise SnapshotChanged("overlay lease changed")
    generation = session.get(StewardGeneration, binding.generation_id, populate_existing=True)
    publication = session.get(StewardPublication, binding.space_id, populate_existing=True)
    if (
        generation is None
        or publication is None
        or publication.generation_id != generation.id
        or generation.status != "published"
        or not valid_generation(session, generation)
        or not steward_inferred.effective_enabled(session, binding.space_id)
        or steward_snapshot.input_versions(session, binding.space_id)
        != intent.payload_json["input_versions"]
    ):
        raise SnapshotChanged("overlay input changed")
    view = session.scalar(
        select(StewardGenerationView).where(
            StewardGenerationView.generation_id == generation.id,
            StewardGenerationView.viewer_account_id == binding.account_id,
            StewardGenerationView.status == "ready",
        )
    )
    if view is None:
        raise SnapshotChanged("overlay viewer changed")
    return intent, view


def _snapshot(
    bind: Engine | Connection,
    binding: OverlayBinding,
) -> tuple[ViewerInput, dict[str, Any], list[dict[str, Any]], set[int]]:
    from app.services.personal_family_view import _inferred_hop_step

    with steward_snapshot.read_transaction(bind) as session:
        intent, view = _require(session, binding)
        active = steward_inferred.active_edges(session, binding.space_id)
        extra = tuple(
            ExtraEdge(
                edge_id=edge.id,
                subject_user_id=edge.subject_user_id,
                object_user_id=edge.object_user_id,
                relation_kind=edge.relation_kind,
            )
            for edge in active
        )
        snapshot = steward_snapshot.viewer_from_session(
            session,
            space_id=binding.space_id,
            account_id=binding.account_id,
            extra_edges=extra,
        )
        account = session.get(Account, binding.account_id)
        assert account is not None
        actor = session.get(User, account.user_id)
        assert actor is not None
        known = {int(node["user_id"]) for node in view.skeleton_json["nodes"]}
        records: list[dict[str, Any]] = []
        for edge in active:
            if (
                edge.subject_user_id not in snapshot.graph.node_genders
                or edge.object_user_id not in snapshot.graph.node_genders
            ):
                continue
            step, step_json = _inferred_hop_step(edge, dict(snapshot.graph.node_genders))
            code = concept_code_for_path((step,), snapshot.graph.node_genders)
            term = resolve_term_from_snapshot(
                snapshot.terms, concept_code=code, structural_description=""
            )
            evidence_ids = [
                int(fact[0])
                for fact in (edge.evidence_json or {}).get("facts", [])
                if isinstance(fact, list | tuple) and fact and isinstance(fact[0], int)
            ]
            added = {edge.subject_user_id, edge.object_user_id} - known
            records.append(
                {
                    "id": edge.id,
                    "revision": edge.revision,
                    "subject_user_id": edge.subject_user_id,
                    "object_user_id": edge.object_user_id,
                    "relation_kind": edge.relation_kind,
                    "term": term["term"],
                    "path": [step_json],
                    "viewer_term": None,
                    "viewer_path": [],
                    "new_user_id": next(iter(added)) if len(added) == 1 else None,
                    "evidence_fact_ids": evidence_ids,
                    "created_at": edge.created_at.isoformat(),
                    "presentation": kinship_presentation.build_inferred_edge_presentation(
                        session,
                        viewer=actor,
                        space_id=binding.space_id,
                        subject_user_id=edge.subject_user_id,
                        object_user_id=edge.object_user_id,
                        term=term["term"],
                        evidence_fact_count=len(evidence_ids),
                    ),
                }
            )
        return snapshot, dict(intent.payload_json["input_versions"]), records, known


def _heartbeat(bind: Engine | Connection, binding: OverlayBinding) -> None:
    with write_transaction(bind) as session:
        intent, _view = _require(session, binding)
        intent.lease_until = utcnow() + timedelta(seconds=config.STEWARD_LEASE_TTL_SECONDS)
        intent.updated_at = utcnow()


def execute(bind: Engine | Connection, binding: OverlayBinding) -> None:
    """Preserve single inferred hop plus the full viewer-relative multi-hop path."""
    try:
        snapshot, versions, records, known = _snapshot(bind, binding)
        for record in records:
            target_id = record["new_user_id"]
            if target_id is None:
                continue
            state = start_search(
                snapshot.graph,
                target_user_id=target_id,
                max_total_expansions=config.STEWARD_SEARCH_MAX_EXPANSIONS,
                max_state_bytes=config.STEWARD_SEARCH_MAX_STATE_BYTES,
            )
            while True:
                _heartbeat(bind, binding)
                result = steward_runtime.run_slice(state)
                if result.resolution is None:
                    state = result.state
                    continue
                resolution = result.resolution
                if resolution.found:
                    path = steps_to_json(resolution.main_path)
                    inferred = [step for step in path if step["fact_id"] < 0]
                    if len(inferred) == 1 and inferred[0]["fact_id"] == -record["id"]:
                        term = resolve_term_from_snapshot(
                            snapshot.terms,
                            concept_code=resolution.concept_code,
                            structural_description=resolution.explanation_structural or "",
                            variant_context=VariantContext(
                                viewer_user_id=snapshot.root_user_id,
                                path=path,
                                births=dict(snapshot.births),
                            ),
                        )
                        record["viewer_path"], record["viewer_term"] = path, term["term"]
                break
        included = known | {
            uid for edge in records for uid in (edge["subject_user_id"], edge["object_user_id"])
        }
        nodes = [
            {**node, "inclusion_reason_code": "inferred_path"}
            for node in json.loads(snapshot.nodes_json)
            if node["user_id"] in included - known
        ]
        payload = jsonable_encoder(
            {
                "nodes": nodes,
                "edges": records,
                "evidence_steps": [
                    [source, step.to_id, step.edge_type, step.subtype, step.direction, step.fact_id]
                    for source, steps in snapshot.graph.adjacency.items()
                    for step in steps
                ],
            }
        )
        if len(json.dumps(payload).encode()) > config.STEWARD_SEARCH_MAX_STATE_BYTES:
            raise RuntimeError("overlay output budget exhausted")
        with write_transaction(bind) as session:
            intent, view = _require(session, binding)
            generation = session.get(StewardGeneration, binding.generation_id)
            assert generation is not None and generation.valid_until is not None
            overlay = session.get(StewardInferredOverlay, (binding.space_id, binding.account_id))
            if overlay is None:
                overlay = StewardInferredOverlay(
                    space_id=binding.space_id, viewer_account_id=binding.account_id
                )
                session.add(overlay)
            overlay.input_versions_json, overlay.payload_json = versions, payload
            overlay.valid_until, overlay.updated_at = generation.valid_until, utcnow()
            view.revision += 1
            view.updated_at = utcnow()
            intent.status, intent.error_code, intent.updated_at = "done", None, utcnow()
            intent.lease_owner, intent.lease_until = None, None
            budget = session.scalar(
                select(StewardRetryBudget).where(
                    StewardRetryBudget.space_id == binding.space_id,
                    StewardRetryBudget.fingerprint == _fingerprint(versions),
                    StewardRetryBudget.scope == f"overlay:{binding.account_id}",
                )
            )
            if budget is not None:
                budget.attempts = max(0, budget.attempts - 1)
                budget.exhausted = budget.attempts >= budget.max_attempts
                budget.retry_after, budget.updated_at = None, utcnow()
    except Exception as exc:
        _fail(
            bind,
            binding,
            changed=isinstance(exc, SnapshotChanged),
            stopping=isinstance(exc, steward_runtime.RuntimeStopping),
        )


def _fail(
    bind: Engine | Connection, binding: OverlayBinding, *, changed: bool, stopping: bool
) -> None:
    with write_transaction(bind) as session:
        intent = session.get(StewardDeliveryIntent, binding.intent_id, populate_existing=True)
        if (
            intent is None
            or intent.status != "pending"
            or intent.lease_owner != binding.owner
            or intent.attempt != binding.attempt
            or intent.lease_until is None
            or intent.lease_until <= utcnow()
        ):
            return
        intent.error_code = (
            "input_changed" if changed else "worker_stopped" if stopping else "overlay_failed"
        )
        intent.status = (
            "superseded"
            if changed
            else "failed"
            if intent.attempt >= config.STEWARD_STAGE_MAX_ATTEMPTS
            else "pending"
        )
        intent.available_at = utcnow() + timedelta(
            seconds=config.STEWARD_RETRY_BACKOFF_FIRST_SECONDS
        )
        intent.lease_owner, intent.lease_until, intent.updated_at = None, None, utcnow()
        budget = session.scalar(
            select(StewardRetryBudget).where(
                StewardRetryBudget.space_id == binding.space_id,
                StewardRetryBudget.fingerprint
                == _fingerprint(intent.payload_json["input_versions"]),
                StewardRetryBudget.scope == f"overlay:{binding.account_id}",
            )
        )
        if budget is not None:
            budget.exhausted = budget.attempts >= budget.max_attempts
            budget.retry_after, budget.updated_at = intent.available_at, utcnow()


def payload_for(
    session: Session,
    *,
    account_id: int,
    space_id: int,
    root_user_id: int,
    core_nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Fresh independent enablement/input fence and every saved viewer path."""
    from app.services.steward_views import _path_valid

    versions = steward_snapshot.input_versions(session, space_id)
    revision = int(versions["global"][2]) + int(versions["space"][2])
    if not steward_inferred.effective_enabled(session, space_id):
        return [], [], revision
    overlay = session.get(StewardInferredOverlay, (space_id, account_id), populate_existing=True)
    if (
        overlay is None
        or overlay.valid_until <= utcnow()
        or overlay.input_versions_json != versions
    ):
        return [], [], revision
    raw = overlay.payload_json
    nodes = list(raw.get("nodes", []))
    visible = {int(node["user_id"]) for node in [*core_nodes, *nodes]}
    witnesses = {tuple(step) for step in raw.get("evidence_steps", [])}
    active = {
        edge.id: edge
        for edge in session.scalars(
            select(StewardInferredEdge).where(
                StewardInferredEdge.space_id == space_id,
                StewardInferredEdge.status == "proposed",
            )
        )
    }
    edges: list[dict[str, Any]] = []
    for saved in raw.get("edges", []):
        edge = dict(saved)
        source = active.get(edge["id"])
        if source is None or source.revision != edge["revision"]:
            continue
        if not _path_valid(
            edge["path"],
            source=source.subject_user_id,
            target=source.object_user_id,
            visible=visible,
            witnesses=witnesses,
        ):
            continue
        if edge.get("viewer_path") and (
            edge.get("new_user_id") is None
            or not _path_valid(
                edge["viewer_path"],
                source=root_user_id,
                target=edge["new_user_id"],
                visible=visible,
                witnesses=witnesses,
            )
        ):
            edge["viewer_path"], edge["viewer_term"] = [], None
        edges.append(edge)
    used = {uid for edge in edges for uid in (edge["subject_user_id"], edge["object_user_id"])}
    return [node for node in nodes if node["user_id"] in used], edges, revision
