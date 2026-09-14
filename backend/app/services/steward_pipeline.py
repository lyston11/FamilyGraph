"""Fenced, versioned Steward computation and atomic publication.

The coordinator owns no long-lived transaction. A view skeleton is committed
before any target search. Complete targets are independently staged and only a
publication pointer makes the sealed, successful generation authoritative.
"""

from __future__ import annotations

import json
import logging
import pickle
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Exists, Text, bindparam, exists, func, insert, literal, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, defer, load_only

from app import config
from app.errors import STEWARD_JOB_NOT_ACTIVE, STEWARD_LEASE_STALE, raise_api_error
from app.models.account import Account
from app.models.space import FamilySpace, SpaceMember
from app.models.steward import (
    StewardDeliveryIntent,
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardRetryBudget,
    StewardViewDemand,
    StewardViewTarget,
)
from app.models.user import User
from app.services import steward_runtime, steward_snapshot
from app.services.relationship_graph import topology_edges_from_facts
from app.services.relationship_resolver import (
    PathStep,
    RelationshipResolution,
    SearchBudgetExceeded,
    SearchState,
    reachable_targets,
    start_search,
    steps_to_json,
)
from app.services.steward_snapshot import SnapshotChanged, ViewerInput, canonical_hash
from app.services.steward_terminology_snapshot import resolve_display_term
from app.utils.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Binding:
    job_id: int
    space_id: int
    owner: str | None
    attempt: int


def binding_for(
    job: StewardJob, *, worker_id: str | None = None, expected_attempt: int | None = None
) -> Binding:
    return Binding(
        job.id,
        job.space_id,
        worker_id if worker_id is not None else job.leased_by,
        expected_attempt if expected_attempt is not None else job.attempt,
    )


@contextmanager
def write_transaction(bind: Engine | Connection) -> Iterator[Session]:
    from app.services.steward import _immediate_tx

    with Session(bind=bind, autoflush=False, expire_on_commit=False) as session:
        with _immediate_tx(session):
            yield session


def require_binding(
    session: Session, binding: Binding, *, now: datetime | None = None
) -> StewardJob:
    moment = now or utcnow()
    job = session.get(StewardJob, binding.job_id, populate_existing=True)
    if job is None or job.status not in ("leased", "running"):
        raise_api_error(409, STEWARD_JOB_NOT_ACTIVE, "作业不在活跃状态")
    if (
        job.space_id != binding.space_id
        or job.attempt != binding.attempt
        or job.leased_by != binding.owner
        or job.lease_expires_at is None
        or job.lease_expires_at <= moment
    ):
        raise_api_error(409, STEWARD_LEASE_STALE, "租约已过期或易主")
    return job


def require_generation(
    session: Session, binding: Binding, generation_id: int, *, now: datetime | None = None
) -> StewardGeneration:
    moment = now or utcnow()
    require_binding(session, binding, now=moment)
    generation = session.get(StewardGeneration, generation_id, populate_existing=True)
    if (
        generation is None
        or generation.job_id != binding.job_id
        or generation.lease_attempt != binding.attempt
        or generation.lease_owner != binding.owner
        or generation.status != "running"
    ):
        raise_api_error(409, STEWARD_LEASE_STALE, "发布代次已失效")
    if (
        generation.valid_until is None
        or generation.valid_until <= moment
        or not steward_snapshot.versions_match(
            session, space_id=binding.space_id, expected=generation.input_versions_json
        )
    ):
        raise SnapshotChanged
    return generation


def valid_generation(
    session: Session, generation: StewardGeneration, *, now: datetime | None = None
) -> bool:
    moment = now or utcnow()
    return bool(
        generation.manifest_sealed
        and generation.valid_until is not None
        and generation.valid_until > moment
        and steward_snapshot.versions_match(
            session, space_id=generation.space_id, expected=generation.input_versions_json
        )
    )


def begin_job(db: Session, binding: Binding, *, now: datetime | None = None) -> StewardJob:
    with write_transaction(db.get_bind()) as session:
        job = require_binding(session, binding, now=now)
        job.status = "running"
        job.updated_at = utcnow()
        session.flush()
    db.expire_all()
    current = db.get(StewardJob, binding.job_id)
    assert current is not None
    return current


def heartbeat(db: Session, binding: Binding, *, ttl: int, now: datetime | None = None) -> datetime:
    moment = now or utcnow()
    with write_transaction(db.get_bind()) as session:
        job = require_binding(session, binding, now=moment)
        generation = session.scalar(
            select(StewardGeneration)
            .where(
                StewardGeneration.job_id == job.id,
                StewardGeneration.lease_attempt == binding.attempt,
                StewardGeneration.status == "running",
            )
            .order_by(StewardGeneration.id.desc())
            .limit(1)
        )
        if generation is not None:
            require_generation(session, binding, generation.id, now=moment)
            generation.updated_at = moment
        expires = moment + timedelta(seconds=ttl)
        job.lease_expires_at = expires
        job.heartbeat_at = moment
        job.updated_at = moment
    db.expire_all()
    return expires


def _view_scope(account_id: int, target_id: int) -> str:
    return f"search:{account_id}:{target_id}"


def search_fingerprint(structural_hash: str) -> str:
    return canonical_hash(
        {"graph": structural_hash, "config": steward_snapshot.search_config_fingerprint()}
    )


class RequiredTargetFailed(RuntimeError):
    """A required target did not complete; never publish or advance its cursor."""


def _reserve_search(
    bind: Engine | Connection,
    binding: Binding,
    generation_id: int,
    *,
    account_id: int,
    target_id: int,
    fingerprint: str,
) -> bool:
    with write_transaction(bind) as session:
        require_generation(session, binding, generation_id)
        budget = session.scalar(
            select(StewardRetryBudget).where(
                StewardRetryBudget.space_id == binding.space_id,
                StewardRetryBudget.fingerprint == fingerprint,
                StewardRetryBudget.scope == _view_scope(account_id, target_id),
            )
        )
        now = utcnow()
        if budget is None:
            budget = StewardRetryBudget(
                space_id=binding.space_id,
                fingerprint=fingerprint,
                scope=_view_scope(account_id, target_id),
                attempts=0,
                max_attempts=config.STEWARD_STAGE_MAX_ATTEMPTS,
                exhausted=False,
                updated_at=now,
            )
            session.add(budget)
        if (
            budget.exhausted
            or budget.attempts >= budget.max_attempts
            or (budget.retry_after is not None and budget.retry_after > now)
        ):
            return False
        budget.attempts += 1
        budget.updated_at = now
        session.flush()
        return True


def _fail_target(
    bind: Engine | Connection,
    binding: Binding,
    generation_id: int,
    view_id: int,
    *,
    target_id: int,
    reason: str,
) -> None:
    with write_transaction(bind) as session:
        generation = require_generation(session, binding, generation_id)
        view = session.get(StewardGenerationView, view_id)
        assert view is not None and view.generation_id == generation.id
        target = session.scalar(
            select(StewardViewTarget).where(
                StewardViewTarget.view_id == view_id, StewardViewTarget.target_user_id == target_id
            )
        )
        assert target is not None
        if target.status in ("ready", "unavailable", "failed"):
            return
        target.status = "failed"
        target.reason_code = reason
        target.updated_at = utcnow()
        if view.status != "failed":
            view.status = "failed"
            generation.failed_views += 1
        view.failed_reason = reason
        view.revision += 1
        view.updated_at = utcnow()
        budget = session.scalar(
            select(StewardRetryBudget).where(
                StewardRetryBudget.space_id == binding.space_id,
                StewardRetryBudget.fingerprint == search_fingerprint(view.structural_hash or ""),
                StewardRetryBudget.scope == _view_scope(view.viewer_account_id, target_id),
            )
        )
        if budget is not None:
            budget.exhausted = budget.attempts >= budget.max_attempts
            budget.retry_after = utcnow() + timedelta(
                seconds=config.STEWARD_RETRY_BACKOFF_FIRST_SECONDS
            )
        generation.updated_at = utcnow()


def encode_resolution(resolution: RelationshipResolution) -> dict[str, Any]:
    return {
        "viewer_user_id": resolution.viewer_user_id,
        "target_user_id": resolution.target_user_id,
        "space_id": resolution.space_id,
        "found": resolution.found,
        "path_class": resolution.path_class,
        "concept_code": resolution.concept_code,
        "main_path": steps_to_json(resolution.main_path),
        "alt_paths": [steps_to_json(path) for path in resolution.alt_paths],
        "alt_descriptions": list(resolution.alt_descriptions),
        "explanation_structural": resolution.explanation_structural,
        "snapshot_hash": resolution.snapshot_hash,
        "node_genders": dict(resolution.node_genders),
    }


def decode_resolution(raw: dict[str, Any]) -> RelationshipResolution:
    def path(values: list[dict[str, Any]]) -> tuple[PathStep, ...]:
        return tuple(
            PathStep(
                from_id=int(step["from"]),
                to_id=int(step["to"]),
                edge_type=str(step["edge_type"]),
                subtype=step.get("subtype"),
                direction=str(step["direction"]),
                fact_id=int(step["fact_id"]),
            )
            for step in values
        )

    return RelationshipResolution(
        viewer_user_id=int(raw["viewer_user_id"]),
        target_user_id=int(raw["target_user_id"]),
        space_id=int(raw["space_id"]),
        found=bool(raw["found"]),
        path_class=str(raw["path_class"]),
        concept_code=raw.get("concept_code"),
        main_path=path(raw["main_path"]),
        alt_paths=tuple(path(value) for value in raw["alt_paths"]),
        alt_descriptions=tuple(raw.get("alt_descriptions", ())),
        explanation_structural=raw.get("explanation_structural"),
        snapshot_hash=str(raw["snapshot_hash"]),
        node_genders={int(key): str(value) for key, value in raw["node_genders"].items()},
    )


def _edge_for(snapshot: ViewerInput, resolution: RelationshipResolution) -> dict[str, Any] | None:
    if not resolution.found:
        return None
    path = steps_to_json(resolution.main_path)
    term = resolve_display_term(
        snapshot,
        target_user_id=resolution.target_user_id,
        concept_code=resolution.concept_code,
        structural_description=resolution.explanation_structural or "",
        path=path,
    )
    return {
        "from_user_id": snapshot.root_user_id,
        "to_user_id": resolution.target_user_id,
        "edge_kind": path[-1]["edge_type"] if path else "self",
        "path": path,
        "alternative_paths": [steps_to_json(value) for value in resolution.alt_paths],
        "path_class": resolution.path_class,
        "concept_code": resolution.concept_code,
        "term": term["term"],
        "term_source_level": term["source_level"],
        "inclusion_reason_code": "confirmed_path",
    }


def save_target(
    bind: Engine | Connection,
    binding: Binding,
    generation_id: int,
    view_id: int,
    *,
    snapshot: ViewerInput,
    resolution: RelationshipResolution,
    reserved: bool = False,
) -> None:
    # Serialization and presentation are deliberately before BEGIN IMMEDIATE.
    raw_json = json.dumps(encode_resolution(resolution))
    edge_json = json.dumps(_edge_for(snapshot, resolution))
    if len(raw_json.encode()) + len(edge_json.encode()) > config.STEWARD_SEARCH_MAX_STATE_BYTES:
        raise SearchBudgetExceeded("state_bytes", 0, config.STEWARD_SEARCH_MAX_STATE_BYTES)
    with write_transaction(bind) as session:
        generation = require_generation(session, binding, generation_id)
        # A target completion only needs progress metadata. Loading the whole
        # view would decode its full family skeleton for every saved target.
        view = session.get(
            StewardGenerationView,
            view_id,
            options=(
                load_only(
                    StewardGenerationView.generation_id,
                    StewardGenerationView.status,
                    StewardGenerationView.completed_count,
                    StewardGenerationView.total_count,
                    StewardGenerationView.revision,
                    raiseload=True,
                ),
            ),
        )
        assert view is not None and view.generation_id == generation.id
        target = session.execute(
            select(StewardViewTarget.id, StewardViewTarget.status).where(
                StewardViewTarget.view_id == view_id,
                StewardViewTarget.target_user_id == resolution.target_user_id,
            )
        ).one_or_none()
        assert target is not None
        target_id, target_status = target
        if target_status in ("ready", "unavailable"):
            return  # lost commit acknowledgement: exactly one completed target
        if target_status != "pending" or view.status == "failed":
            raise RequiredTargetFailed
        # Text binds preserve the already encoded JSON document; the mapped
        # JSON columns still decode to dict/None on reads. Passing these strings
        # through a JSON bind would double-encode them inside the writer.
        session.execute(
            update(StewardViewTarget)
            .where(StewardViewTarget.id == target_id, StewardViewTarget.status == "pending")
            .values(
                status="ready" if resolution.found else "unavailable",
                resolution_json=bindparam("resolution_payload", raw_json, type_=Text()),
                edge_json=bindparam("edge_payload", edge_json, type_=Text()),
                reason_code=None if resolution.found else "no_path",
                updated_at=utcnow(),
            )
            .returning(StewardViewTarget.id)
            .execution_options(synchronize_session=False)
        ).scalar_one()
        if reserved:
            budget = session.scalar(
                select(StewardRetryBudget).where(
                    StewardRetryBudget.space_id == binding.space_id,
                    StewardRetryBudget.fingerprint
                    == search_fingerprint(snapshot.graph.snapshot_hash),
                    StewardRetryBudget.scope
                    == _view_scope(snapshot.account_id, resolution.target_user_id),
                )
            )
            if budget is not None:
                # A completed target is not a failed/abandoned attempt. This
                # refund shares the exactly-once pending->complete transition.
                budget.attempts = max(0, budget.attempts - 1)
                budget.exhausted = budget.attempts >= budget.max_attempts
                budget.retry_after, budget.updated_at = None, utcnow()
        view.completed_count += 1
        view.revision += 1
        view.updated_at = utcnow()
        if view.completed_count == view.total_count:
            view.status = "ready"
            generation.ready_views += 1
        generation.updated_at = utcnow()


@dataclass
class _Work:
    view_id: int
    snapshot: ViewerInput
    pending: list[int]
    prior_view_id: int | None
    state: SearchState | None = None
    target_id: int | None = None


def _take_work(queue: deque[_Work], foreground: set[int], prefer_foreground: bool) -> _Work:
    for work in queue:
        if (work.snapshot.account_id in foreground) == prefer_foreground:
            queue.remove(work)
            return work
    return queue.popleft()


def _snapshot_header(bind: Engine | Connection, binding: Binding, upper: int) -> dict[str, Any]:
    from app.services import steward

    with steward_snapshot.read_transaction(bind) as session:
        job = require_binding(session, binding)
        space = session.get(FamilySpace, binding.space_id)
        assert space is not None
        versions = steward_snapshot.input_versions(session, space.id)
        viewers = session.execute(
            select(Account.id, Account.user_id)
            .join(SpaceMember, SpaceMember.user_id == Account.user_id)
            .join(User, User.id == Account.user_id)
            .where(
                SpaceMember.space_id == space.id,
                SpaceMember.status == "active",
                User.deleted_at.is_(None),
            )
            .order_by(Account.id)
        ).all()
        demands = {
            row.viewer_account_id: (row.revision, row.focus_user_id, row.requested_at)
            for row in session.scalars(
                select(StewardViewDemand).where(StewardViewDemand.space_id == space.id)
            )
        }
        floor = steward._completed_cursor_floor(session, job)
        window = steward._consume_window(session, space, floor=floor, upper=upper)
        return {
            "versions": versions,
            "valid_until": steward_snapshot.valid_until(session, space_id=space.id, now=utcnow()),
            "viewers": [(int(a), int(u)) for a, u in viewers],
            "demands": demands,
            "floor": floor,
            "events": len(window.events),
        }


def _prepare_delivery(
    bind: Engine | Connection,
    binding: Binding,
    generation_id: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    from app.services import steward, steward_delivery

    with steward_snapshot.read_transaction(bind) as session:
        require_generation(session, binding, generation_id)
        job = require_binding(session, binding)
        space = session.get(FamilySpace, binding.space_id)
        assert space is not None
        visible = steward._space_visible_user_ids(session, space)
        findings = {
            item["signature"]: item for item in steward._detect_findings(session, space, visible)
        }
        intents = steward_delivery.prepare_intents(
            session,
            job=job,
            space=space,
            visible=visible,
            findings=list(findings.values()),
            generation_id=generation_id,
        )
        return intents, sorted(findings)


def _latest_view(
    session: Session, *, space_id: int, account_id: int, structural_hash: str | None = None
) -> StewardGenerationView | None:
    stmt = (
        select(StewardGenerationView)
        .where(
            StewardGenerationView.space_id == space_id,
            StewardGenerationView.viewer_account_id == account_id,
            StewardGenerationView.structural_hash.is_not(None),
        )
        .options(defer(StewardGenerationView.skeleton_json, raiseload=True))
    )
    if structural_hash is not None:
        stmt = stmt.where(StewardGenerationView.structural_hash == structural_hash)
    return session.scalar(stmt.order_by(StewardGenerationView.generation_id.desc()).limit(1))


def _stage_view(
    bind: Engine | Connection,
    binding: Binding,
    generation_id: int,
    *,
    snapshot: ViewerInput,
    demand_revision: int,
) -> _Work | None:
    reached = reachable_targets(snapshot.graph)
    target_ids = sorted(
        (uid for uid in reached if uid != snapshot.root_user_id),
        key=lambda uid: (reached[uid], uid),
    )
    nodes = [node for node in json.loads(snapshot.nodes_json) if node["user_id"] in reached]
    facts = tuple(
        fact
        for fact in snapshot.graph.confirmed_facts
        if fact.subject_user_id in reached and fact.object_user_id in reached
    )
    topology = topology_edges_from_facts(facts)  # type: ignore[arg-type]
    structural_hash = snapshot.graph.snapshot_hash
    presentation_hash = canonical_hash(
        {
            "structural": structural_hash,
            "terms": [asdict(entry) for entry in snapshot.terms.entries],
            "terminology": snapshot.terminology.fingerprint,
            "births": snapshot.births,
            "nodes": nodes,
            "policy": config.POLICY_VERSION,
            "config": steward_snapshot.config_fingerprint(),
        }
    )
    skeleton = {
        "nodes": nodes,
        "topology_edges": topology,
        "target_ids": target_ids,
        "evidence_steps": [
            [from_id, step.to_id, step.edge_type, step.subtype, step.direction, step.fact_id]
            for from_id, steps in snapshot.graph.adjacency.items()
            for step in steps
            if step.fact_id >= 0
        ],
    }
    topology_revision = canonical_hash({"nodes": sorted(reached), "topology_edges": topology})
    with write_transaction(bind) as session:
        generation = require_generation(session, binding, generation_id)
        previous = _latest_view(
            session,
            space_id=binding.space_id,
            account_id=snapshot.account_id,
            structural_hash=structural_hash,
        )
        if previous is not None:
            prior_generation = session.get(StewardGeneration, previous.generation_id)
            if (
                prior_generation is None
                or prior_generation.input_versions_json.get("search_config")
                != steward_snapshot.search_config_fingerprint()
            ):
                previous = None
        previous_id = (previous.result_view_id or previous.id) if previous is not None else None
        reuse = (
            previous is not None
            and previous.status == "ready"
            and previous.presentation_hash == presentation_hash
        )
        now = utcnow()
        view = StewardGenerationView(
            generation_id=generation_id,
            space_id=binding.space_id,
            viewer_account_id=snapshot.account_id,
            root_user_id=snapshot.root_user_id,
            status="ready" if reuse or not target_ids else "pending",
            completed_count=len(target_ids) if reuse else 0,
            total_count=len(target_ids),
            revision=1,
            structural_hash=structural_hash,
            presentation_hash=presentation_hash,
            topology_revision=topology_revision,
            skeleton_json=skeleton,
            demand_revision=demand_revision,
            result_view_id=previous_id if reuse else None,
            created_at=now,
            updated_at=now,
        )
        session.add(view)
        session.flush()
        if view.status == "ready":
            generation.ready_views += 1
        view_id = view.id
    if reuse or not target_ids:
        return None
    # Target manifests are written in bounded batches, before any target search.
    for offset in range(0, len(target_ids), 32):
        with write_transaction(bind) as session:
            require_generation(session, binding, generation_id)
            session.add_all(
                [
                    StewardViewTarget(
                        view_id=view_id,
                        target_user_id=uid,
                        status="pending",
                        distance=reached[uid],
                        updated_at=utcnow(),
                    )
                    for uid in target_ids[offset : offset + 32]
                ]
            )
    return _Work(view_id, snapshot, target_ids, previous_id)


def _reuse_view(
    bind: Engine | Connection,
    binding: Binding,
    generation_id: int,
    *,
    account_id: int,
    root_user_id: int,
    demand_revision: int,
    versions: dict[str, Any],
) -> bool:
    """Cheap cache check before load_graph or any search, including warm scans."""
    with write_transaction(bind) as session:
        generation = require_generation(session, binding, generation_id)
        prior = _latest_view(session, space_id=binding.space_id, account_id=account_id)
        if prior is None or prior.status != "ready" or prior.root_user_id != root_user_id:
            return False
        old_generation = session.get(StewardGeneration, prior.generation_id)
        if (
            old_generation is None
            or not valid_generation(session, old_generation)
            or steward_snapshot.core_versions(old_generation.input_versions_json)
            != steward_snapshot.core_versions(versions)
        ):
            return False
        now = utcnow()
        # Copy one already-authorized immutable skeleton within SQLite. Loading
        # and re-encoding the family JSON here would extend every warm writer.
        view = StewardGenerationView
        session.execute(
            insert(view)
            .from_select(
                (
                    "generation_id",
                    "space_id",
                    "viewer_account_id",
                    "root_user_id",
                    "status",
                    "completed_count",
                    "total_count",
                    "revision",
                    "structural_hash",
                    "presentation_hash",
                    "topology_revision",
                    "skeleton_json",
                    "demand_revision",
                    "result_view_id",
                    "created_at",
                    "updated_at",
                ),
                select(
                    literal(generation_id),
                    view.space_id,
                    view.viewer_account_id,
                    view.root_user_id,
                    literal("ready"),
                    view.completed_count,
                    view.total_count,
                    literal(1),
                    view.structural_hash,
                    view.presentation_hash,
                    view.topology_revision,
                    view.skeleton_json,
                    literal(demand_revision),
                    func.coalesce(view.result_view_id, view.id),
                    literal(now),
                    literal(now),
                ).where(view.id == prior.id),
                include_defaults=False,
            )
            .returning(view.id)
        ).scalar_one()
        generation.ready_views += 1
        return True


def _cached_resolution(
    bind: Engine | Connection, work: _Work, target_id: int
) -> RelationshipResolution | None:
    if work.prior_view_id is None:
        return None
    with Session(bind=bind) as session:
        row = session.scalar(
            select(StewardViewTarget).where(
                StewardViewTarget.view_id == work.prior_view_id,
                StewardViewTarget.target_user_id == target_id,
                StewardViewTarget.status.in_(("ready", "unavailable")),
            )
        )
        if row is not None and row.resolution_json is not None:
            resolution = decode_resolution(row.resolution_json)
            if resolution.snapshot_hash == work.snapshot.graph.snapshot_hash:
                return resolution
    return None


def _pick_target(bind: Engine | Connection, work: _Work) -> int:
    with Session(bind=bind) as session:
        demand = session.scalar(
            select(StewardViewDemand.focus_user_id).where(
                StewardViewDemand.space_id == work.snapshot.graph.space_id,
                StewardViewDemand.viewer_account_id == work.snapshot.account_id,
            )
        )
    if demand in work.pending:
        assert isinstance(demand, int)
        work.pending.remove(demand)
        return demand
    return work.pending.pop(0)


def execute(db: Session, job: StewardJob, *, now: datetime, upper: int) -> dict[str, Any]:
    binding = binding_for(job)
    bind = db.get_bind()
    header = _snapshot_header(bind, binding, upper)
    versions: dict[str, Any] = header["versions"]
    stats: dict[str, int] = {
        "events_consumed": header["events"],
        "derived_recomputed": 0,
        "cards_created": 0,
        "cards_superseded": 0,
        "findings_emitted": 0,
        "cards_expired": 0,
        "personal_family_views_rebuilt": 0,
        "inferred_projected": 0,
        "inferred_superseded": 0,
        "suggestions_projected": 0,
        "terminology_projections": 0,
        "terminology_suggestions": 0,
    }
    with write_transaction(bind) as session:
        require_binding(session, binding)
        if not steward_snapshot.versions_match(
            session, space_id=binding.space_id, expected=versions
        ):
            raise SnapshotChanged
        # Only generations owned by an older attempt of this job are retired.
        session.execute(
            update(StewardGeneration)
            .where(
                StewardGeneration.job_id == binding.job_id,
                StewardGeneration.status == "running",
                StewardGeneration.lease_attempt < binding.attempt,
            )
            .values(status="superseded", updated_at=utcnow())
        )
        generation = StewardGeneration(
            space_id=binding.space_id,
            job_id=binding.job_id,
            status="running",
            execution_cursor=upper,
            fingerprint=canonical_hash(steward_snapshot.core_versions(versions)),
            input_versions_json=versions,
            lease_owner=binding.owner,
            lease_attempt=binding.attempt,
            valid_until=header["valid_until"],
            required_views=len(header["viewers"]),
            manifest_sealed=True,
            stats_json={},
            created_at=now,
            updated_at=now,
        )
        session.add(generation)
        session.flush()
        generation_id = generation.id
    # Preparation is interleaved with search: the first viewer need not wait
    # for every other viewer's graph snapshot before its first completed term.
    viewers: list[tuple[int, int]] = list(header["viewers"])
    pending_views: deque[_Work] = deque()
    active: deque[_Work] = deque()
    snapshot_bytes: dict[int, int] = {}
    turn = 0
    while viewers or pending_views or active:
        if steward_runtime.is_stopping():
            raise steward_runtime.RuntimeStopping
        with steward_snapshot.read_transaction(bind) as session:
            require_generation(session, binding, generation_id)
            demand_rows = list(
                session.scalars(
                    select(StewardViewDemand).where(
                        StewardViewDemand.space_id == binding.space_id,
                    )
                )
            )
            demands = {
                row.viewer_account_id: (row.revision, row.requested_at) for row in demand_rows
            }
            foreground = {
                row.viewer_account_id
                for row in demand_rows
                if row.revision > row.fulfilled_revision
            }
        if viewers:
            viewers.sort(
                key=lambda pair: (
                    0 if pair[0] in foreground else 1,
                    -demands[pair[0]][1].timestamp() if pair[0] in foreground else 0,
                    pair[0],
                )
            )
            account_id, root_user_id = viewers.pop(0)
            demand_revision = demands[account_id][0] if account_id in demands else 0
            if not _reuse_view(
                bind,
                binding,
                generation_id,
                account_id=account_id,
                root_user_id=root_user_id,
                demand_revision=demand_revision,
                versions=versions,
            ):
                snapshot = steward_snapshot.read_viewer(
                    bind,
                    space_id=binding.space_id,
                    account_id=account_id,
                    expected_versions=versions,
                )
                size = len(pickle.dumps(snapshot, protocol=5))
                if size + sum(snapshot_bytes.values()) > config.STEWARD_SNAPSHOT_MAX_BYTES:
                    raise RequiredTargetFailed("snapshot_budget")
                prepared = _stage_view(
                    bind,
                    binding,
                    generation_id,
                    snapshot=snapshot,
                    demand_revision=demand_revision,
                )
                stats["personal_family_views_rebuilt"] += 1
                if prepared is not None:
                    snapshot_bytes[prepared.view_id] = size
                    pending_views.append(prepared)
        # Three foreground slices, then one background slice. Each queue
        # rotates after a slice/target, so neither a busy viewer nor a hard
        # target can starve other demanded viewers or background completion.
        prefer_foreground = turn % 4 != 3
        turn += 1
        while pending_views and len(active) < 4:
            active.append(_take_work(pending_views, foreground, prefer_foreground))
        if not active:
            continue
        work = _take_work(active, foreground, prefer_foreground)
        try:
            if work.target_id is None:
                work.target_id = _pick_target(bind, work)
                cached = _cached_resolution(bind, work, work.target_id)
                if cached is not None:
                    save_target(
                        bind,
                        binding,
                        generation_id,
                        work.view_id,
                        snapshot=work.snapshot,
                        resolution=cached,
                    )
                    work.target_id = None
                    if work.pending:
                        pending_views.append(work)
                    else:
                        snapshot_bytes.pop(work.view_id, None)
                    continue
                if not _reserve_search(
                    bind,
                    binding,
                    generation_id,
                    account_id=work.snapshot.account_id,
                    target_id=work.target_id,
                    fingerprint=search_fingerprint(work.snapshot.graph.snapshot_hash),
                ):
                    raise RequiredTargetFailed("retry_budget_exhausted")
                work.state = start_search(
                    work.snapshot.graph,
                    target_user_id=work.target_id,
                    max_total_expansions=config.STEWARD_SEARCH_MAX_EXPANSIONS,
                    max_state_bytes=config.STEWARD_SEARCH_MAX_STATE_BYTES,
                )
            assert work.state is not None and work.target_id is not None
            result = steward_runtime.run_slice(work.state)
            work.state = result.state
            if result.resolution is None:
                active.append(work)
                continue
            save_target(
                bind,
                binding,
                generation_id,
                work.view_id,
                snapshot=work.snapshot,
                resolution=result.resolution,
                reserved=True,
            )
            stats["derived_recomputed"] += 1
            work.target_id, work.state = None, None
            if work.pending:
                pending_views.append(work)
            else:
                snapshot_bytes.pop(work.view_id, None)
        except (SnapshotChanged, steward_runtime.RuntimeStopping):
            raise
        except Exception as exc:
            reason = (
                "search_budget_exhausted"
                if isinstance(exc, SearchBudgetExceeded)
                else str(exc)
                if isinstance(exc, RequiredTargetFailed)
                else "target_failed"
            )
            assert work.target_id is not None
            _fail_target(
                bind,
                binding,
                generation_id,
                work.view_id,
                target_id=work.target_id,
                reason=reason,
            )
            raise RequiredTargetFailed(reason) from exc
    if stats["personal_family_views_rebuilt"] == 0:
        stats["fingerprint_short_circuit"] = 1
    intents, signatures = _prepare_delivery(bind, binding, generation_id)
    for offset in range(0, len(intents), 16):
        with write_transaction(bind) as session:
            require_generation(session, binding, generation_id)
            session.add_all(
                [
                    StewardDeliveryIntent(
                        generation_id=generation_id,
                        space_id=binding.space_id,
                        intent_key=intent["key"],
                        kind=intent["kind"],
                        payload_json=intent["payload"],
                        effect_fingerprint=intent["effect_fingerprint"],
                        status="pending",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    for intent in intents[offset : offset + 16]
                ]
            )
    with write_transaction(bind) as session:
        generation = require_generation(session, binding, generation_id)
        generation.intents_prepared = True
        generation.stats_json = {"stats": stats, "finding_signatures": signatures}
        generation.updated_at = utcnow()
    return {
        "floor_cursor": header["floor"],
        "trigger_cursor": upper,
        "generation_id": generation_id,
        "finding_signatures": signatures,
        "stats": stats,
    }


def _eligible_demand_viewer() -> Exists:
    return exists(
        select(Account.id)
        .join(User, User.id == Account.user_id)
        .join(SpaceMember, SpaceMember.user_id == Account.user_id)
        .where(
            Account.id == StewardViewDemand.viewer_account_id,
            SpaceMember.space_id == StewardViewDemand.space_id,
            SpaceMember.status == "active",
            User.deleted_at.is_(None),
        )
    )


def _has_successor_demand(session: Session, *, space_id: int, generation_id: int) -> bool:
    satisfied = exists(
        select(StewardGenerationView.id).where(
            StewardGenerationView.generation_id == generation_id,
            StewardGenerationView.viewer_account_id == StewardViewDemand.viewer_account_id,
            StewardGenerationView.demand_revision >= StewardViewDemand.revision,
        )
    )
    return (
        session.scalar(
            select(StewardViewDemand.id)
            .where(
                StewardViewDemand.space_id == space_id,
                StewardViewDemand.revision > StewardViewDemand.fulfilled_revision,
                _eligible_demand_viewer(),
                ~satisfied,
            )
            .limit(1)
        )
        is not None
    )


def _enqueue_successor(session: Session, job: StewardJob, *, now: datetime) -> None:
    from app.services.steward import _enqueue_core_job_locked, current_event_watermark

    _enqueue_core_job_locked(
        session,
        space_id=job.space_id,
        cause="integrity_scan",
        trigger_cursor=max(job.trigger_cursor, current_event_watermark(session)),
        now=now,
        respect_succeeded_shortcircuit=False,
    )


def publish(db: Session, binding: Binding, *, summary: dict[str, Any], upper: int) -> None:
    from app.services import steward_events
    from app.services.domain_events import emit

    generation_id = int(summary["generation_id"])
    with write_transaction(db.get_bind()) as session:
        generation = require_generation(session, binding, generation_id)
        job = require_binding(session, binding)
        # Counts are maintained in the same transactions as unique result rows.
        # The indexed NOT EXISTS check also guards incomplete manifest writes.
        incomplete = session.scalar(
            select(StewardGenerationView.id)
            .where(
                StewardGenerationView.generation_id == generation_id,
                StewardGenerationView.status != "ready",
            )
            .limit(1)
        )
        view_count = session.scalar(
            select(func.count())
            .select_from(StewardGenerationView)
            .where(StewardGenerationView.generation_id == generation_id)
        )
        if (
            not generation.manifest_sealed
            or not generation.intents_prepared
            or generation.failed_views
            or generation.ready_views != generation.required_views
            or view_count != generation.required_views
            or incomplete is not None
            or generation.execution_cursor != upper
        ):
            raise RequiredTargetFailed("required_manifest_incomplete")
        now = utcnow()
        publication = session.get(StewardPublication, binding.space_id)
        if publication is None:
            publication = StewardPublication(
                space_id=binding.space_id, generation_id=generation_id, updated_at=now
            )
            session.add(publication)
        else:
            publication.generation_id, publication.updated_at = generation_id, now
        generation.status, generation.published_at, generation.updated_at = "published", now, now
        job.status, job.last_event_cursor = "succeeded", upper
        job.error_code, job.error_json, job.available_at = None, None, None
        # Large plans already reside in staging. The settlement record is bounded.
        job.checkpoint_json = {
            "last_event_cursor": upper,
            "policy_version": job.policy_version,
            "generation_id": generation_id,
            "finding_signatures": summary["finding_signatures"][:128],
            "stats": summary["stats"],
        }
        captured_demand = (
            select(StewardGenerationView.demand_revision)
            .where(
                StewardGenerationView.generation_id == generation_id,
                StewardGenerationView.viewer_account_id == StewardViewDemand.viewer_account_id,
            )
            .scalar_subquery()
        )
        session.execute(
            update(StewardViewDemand)
            .where(
                StewardViewDemand.space_id == binding.space_id,
                captured_demand > StewardViewDemand.fulfilled_revision,
            )
            .values(fulfilled_revision=captured_demand)
            .execution_options(synchronize_session=False)
        )
        # An authorization loss consumes the request without serving a result.
        # Preserve its retry/cooldown history, but don't schedule this account
        # forever. A later authorized request gets a new revision.
        session.execute(
            update(StewardViewDemand)
            .where(
                StewardViewDemand.space_id == binding.space_id,
                StewardViewDemand.revision > StewardViewDemand.fulfilled_revision,
                ~_eligible_demand_viewer(),
            )
            .values(fulfilled_revision=StewardViewDemand.revision, focus_user_id=None)
            .execution_options(synchronize_session=False)
        )
        job.settled_at, job.updated_at = now, now
        session.flush()
        emit(
            session,
            event_type=steward_events.EVENT_STEWARD_JOB_COMPLETED,
            aggregate_type=steward_events.AGGREGATE_STEWARD_JOB,
            aggregate_id=job.id,
            payload={
                "job_id": job.id,
                "space_id": job.space_id,
                "cause": job.cause,
                "status": "succeeded",
                "trigger_cursor": upper,
                "stats": summary["stats"],
            },
            space_id=job.space_id,
            actor_account_id=None,
        )
        if job.trigger_cursor > upper or _has_successor_demand(
            session, space_id=binding.space_id, generation_id=generation_id
        ):
            _enqueue_successor(session, job, now=now)
    db.expire_all()


def record_failure(db: Session, binding: Binding, *, error_code: str, retryable: bool) -> None:
    """Late failures cannot poison another attempt, even with the same owner."""
    from app.services import steward_events
    from app.services.domain_events import emit

    with write_transaction(db.get_bind()) as session:
        job = session.get(StewardJob, binding.job_id, populate_existing=True)
        if (
            job is None
            or job.status not in ("leased", "running")
            or job.attempt != binding.attempt
            or job.leased_by != binding.owner
        ):
            return
        now = utcnow()
        if job.lease_expires_at is None or job.lease_expires_at <= now:
            return  # only the expiry reaper may recover this binding
        generation = session.scalar(
            select(StewardGeneration)
            .where(
                StewardGeneration.job_id == binding.job_id,
                StewardGeneration.lease_attempt == binding.attempt,
                StewardGeneration.lease_owner == binding.owner,
                StewardGeneration.status == "running",
            )
            .order_by(StewardGeneration.id.desc())
            .limit(1)
        )
        changed = error_code == "input_changed" or (
            generation is not None
            and (
                generation.valid_until is None
                or generation.valid_until <= now
                or not steward_snapshot.versions_match(
                    session, space_id=binding.space_id, expected=generation.input_versions_json
                )
            )
        )
        if generation is not None:
            generation.status = "superseded" if changed else "failed"
            generation.error_code = "input_changed" if changed else error_code
            generation.updated_at = now
        if retryable and not changed and job.attempt < job.max_attempts:
            job.status = "queued"
            job.available_at = now + timedelta(
                seconds=config.STEWARD_RETRY_BACKOFF_FIRST_SECONDS
                if job.attempt <= 1
                else config.STEWARD_RETRY_BACKOFF_SECOND_SECONDS
            )
            job.leased_by, job.lease_expires_at, job.heartbeat_at = None, None, None
        else:
            job.status, job.settled_at = "failed", now
        job.error_code, job.error_json, job.updated_at = (
            "input_changed" if changed else error_code,
            {"code": "input_changed" if changed else error_code},
            now,
        )
        session.flush()
        if changed:
            _enqueue_successor(session, job, now=now)
        elif job.status == "failed":
            emit(
                session,
                event_type=steward_events.EVENT_STEWARD_JOB_FAILED,
                aggregate_type=steward_events.AGGREGATE_STEWARD_JOB,
                aggregate_id=job.id,
                payload={
                    "job_id": job.id,
                    "space_id": job.space_id,
                    "cause": job.cause,
                    "status": "failed",
                    "attempt": job.attempt,
                    "error": {"code": error_code},
                },
                space_id=job.space_id,
                actor_account_id=None,
            )
    db.expire_all()


def published_pair_resolution(
    session: Session, *, viewer_user_id: int, target_user_id: int, space_id: int, graph_hash: str
) -> RelationshipResolution | None:
    publication = session.get(StewardPublication, space_id, populate_existing=True)
    if publication is None:
        return None
    generation = session.get(StewardGeneration, publication.generation_id, populate_existing=True)
    # The caller just built an authorized current graph. Its content hash is a
    # stronger structural guard than the broad source revision, and allows term
    # changes to reuse paths. Config still fences algorithm changes explicitly.
    if (
        generation is None
        or generation.status != "published"
        or not generation.manifest_sealed
        or generation.input_versions_json.get("search_config")
        != steward_snapshot.search_config_fingerprint()
    ):
        return None
    view = session.scalar(
        select(StewardGenerationView).where(
            StewardGenerationView.generation_id == generation.id,
            StewardGenerationView.root_user_id == viewer_user_id,
            StewardGenerationView.structural_hash == graph_hash,
            StewardGenerationView.status == "ready",
        )
    )
    if view is None:
        return None
    target = session.scalar(
        select(StewardViewTarget).where(
            StewardViewTarget.view_id == (view.result_view_id or view.id),
            StewardViewTarget.target_user_id == target_user_id,
            StewardViewTarget.status.in_(("ready", "unavailable")),
        )
    )
    if target is None or target.resolution_json is None:
        return None
    return decode_resolution(target.resolution_json)
