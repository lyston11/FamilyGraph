"""Read one authorized generation: publication for legacy, preview for opt-in.

Every read verifies current account/membership and the complete durable input
revision fence in its explicit read snapshot. This validates saved authorization
and fact witnesses without doing a graph search or N queries per alternative
path. Time expiry is checked before either data or a renewed ETag is served.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.errors import PERSONAL_FAMILY_VIEW_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.space import FamilySpace, SpaceMember
from app.models.steward import (
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardViewTarget,
)
from app.models.user import User
from app.services.steward_pipeline import valid_generation
from app.utils.timeutil import utcnow


def authorize(session: Session, *, account: Account, space_id: int) -> Account:
    expected_user_id, expected_token_version = account.user_id, account.token_version
    current = session.get(Account, account.id, populate_existing=True)
    if (
        current is None
        or current.user_id != expected_user_id
        or current.token_version != expected_token_version
    ):
        raise_api_error(401, "AUTH_INVALID_CREDENTIALS", "登录状态已失效")
    actor = session.get(User, current.user_id)
    member = session.scalar(
        select(SpaceMember.id).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == current.user_id,
            SpaceMember.status == "active",
        )
    )
    if (
        actor is None
        or actor.deleted_at is not None
        or member is None
        or session.get(FamilySpace, space_id) is None
    ):
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    return current


def _progress(
    *,
    generation: int,
    revision: int,
    phase: str,
    targets: list[dict[str, Any]],
    topology: str = "",
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": "pfv-progress-v1",
        "phase": phase,
        "generation": generation,
        "revision": revision,
        "topology_revision": topology,
        "completed_count": sum(target["status"] in ("ready", "unavailable") for target in targets),
        "total_count": len(targets),
        "targets": targets,
        "reason_code": reason,
        "next_poll_ms": 0
        if phase in ("ready", "failed")
        else 250
        if phase in ("queued", "preparing")
        else 1000,
    }


def worker_stopped(session: Session, *, space_id: int) -> bool:
    """A disabled scheduler can still have an explicitly leased job finishing."""
    if config.STEWARD_ENABLED and config.STEWARD_WORKER_ENABLED:
        return False
    return (
        session.scalar(
            select(StewardJob.id)
            .where(
                StewardJob.space_id == space_id,
                StewardJob.status.in_(("leased", "running")),
                StewardJob.lease_expires_at > utcnow(),
            )
            .limit(1)
        )
        is None
    )


def _empty(
    *,
    space_id: int,
    generation: StewardGeneration,
    view: StewardGenerationView | None,
    progressive: bool,
    reason: str,
    revision_extra: int = 0,
) -> dict[str, Any]:
    failed = reason == "worker_stopped" or (
        generation.status == "failed" and reason != "input_changed"
    )
    phase = "failed" if failed else "preparing" if reason == "preparing" else "retrying"
    payload: dict[str, Any] = {
        "space_id": space_id,
        "status": "failed" if failed else "running" if phase == "preparing" else "stale",
        "view_version": generation.id,
        "computed_at": None,
        "nodes": [],
        "edges": [],
        "inferred_edges": [],
        "topology_edges": [],
        "truncated": False,
        "next_cursor": None,
        "stale_reason": reason,
    }
    if progressive:
        # An invalidation must sort after the last valid response even before a
        # successor generation has been prepared. Revisions never reset to 0.
        revision = ((view.revision + 1) if view is not None else 0) + revision_extra
        payload["progress"] = _progress(
            generation=generation.id, revision=revision, phase=phase, targets=[], reason=reason
        )
    return payload


def _path_valid(
    path: Any, *, source: int, target: int, visible: set[int], witnesses: set[tuple[Any, ...]]
) -> bool:
    if not isinstance(path, list) or not path:
        return source == target
    cursor = source
    for step in path:
        if not isinstance(step, dict) or step.get("from") != cursor:
            return False
        key = (
            step.get("from"),
            step.get("to"),
            step.get("edge_type"),
            step.get("subtype"),
            step.get("direction"),
            step.get("fact_id"),
        )
        if key not in witnesses or step.get("from") not in visible or step.get("to") not in visible:
            return False
        cursor = step["to"]
    return cursor == target


def payload_for(
    session: Session, *, account: Account, space_id: int, progressive: bool
) -> tuple[dict[str, Any] | None, datetime | None]:
    from app.services import steward_overlay, steward_snapshot

    current = authorize(session, account=account, space_id=space_id)
    current_versions = steward_snapshot.input_versions(session, space_id)
    revision_extra = int(current_versions["global"][2]) + int(current_versions["space"][2])
    publication = session.get(StewardPublication, space_id, populate_existing=True)
    published = (
        session.get(StewardGeneration, publication.generation_id, populate_existing=True)
        if publication is not None
        else None
    )
    latest = session.scalar(
        select(StewardGeneration)
        .where(StewardGeneration.space_id == space_id, StewardGeneration.manifest_sealed.is_(True))
        .order_by(StewardGeneration.id.desc())
        .limit(1)
    )
    generation = latest if progressive else published
    if generation is None:
        if latest is None:
            return None, None  # explicit legacy helper compatibility
        generation = latest
    view = session.scalar(
        select(StewardGenerationView).where(
            StewardGenerationView.generation_id == generation.id,
            StewardGenerationView.viewer_account_id == current.id,
            StewardGenerationView.root_user_id == current.user_id,
        )
    )
    if not valid_generation(session, generation):
        return _empty(
            space_id=space_id,
            generation=generation,
            view=view,
            progressive=progressive,
            reason="worker_stopped"
            if worker_stopped(session, space_id=space_id)
            else "input_changed",
            revision_extra=revision_extra,
        ), None
    if view is None:
        return _empty(
            space_id=space_id,
            generation=generation,
            view=None,
            progressive=progressive,
            reason="worker_stopped" if worker_stopped(session, space_id=space_id) else "preparing",
            revision_extra=revision_extra,
        ), generation.valid_until
    is_published = (
        publication is not None
        and publication.generation_id == generation.id
        and generation.status == "published"
    )
    if not progressive and not is_published:
        return _empty(
            space_id=space_id,
            generation=generation,
            view=view,
            progressive=False,
            reason="preparing",
            revision_extra=revision_extra,
        ), generation.valid_until
    skeleton = view.skeleton_json
    nodes = list(skeleton.get("nodes", []))
    visible = {int(node["user_id"]) for node in nodes}
    witnesses = {tuple(step) for step in skeleton.get("evidence_steps", [])}
    rows = session.execute(
        select(
            StewardViewTarget.target_user_id,
            StewardViewTarget.status,
            StewardViewTarget.reason_code,
            StewardViewTarget.edge_json,
        )
        .where(StewardViewTarget.view_id == (view.result_view_id or view.id))
        .order_by(StewardViewTarget.target_user_id)
    ).all()
    by_id = {row.target_user_id: row for row in rows}
    targets: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for target_id in skeleton.get("target_ids", []):
        if target_id not in visible or target_id == current.user_id:
            continue
        row = by_id.get(target_id)
        status = row.status if row is not None else "pending"
        reason = row.reason_code if row is not None else None
        if row is not None and row.status == "ready" and row.edge_json is not None:
            edge = dict(row.edge_json)
            if not _path_valid(
                edge.get("path"),
                source=current.user_id,
                target=target_id,
                visible=visible,
                witnesses=witnesses,
            ):
                status, reason = "failed", "evidence_invalid"
            else:
                edge["alternative_paths"] = [
                    path
                    for path in edge.get("alternative_paths", [])
                    if _path_valid(
                        path,
                        source=current.user_id,
                        target=target_id,
                        visible=visible,
                        witnesses=witnesses,
                    )
                ]
                edges.append(edge)
        targets.append({"user_id": target_id, "status": status, "reason_code": reason})
    phase, reason = "building", None
    if any(target["status"] == "failed" for target in targets) or generation.status == "failed":
        phase, reason = "failed", view.failed_reason or generation.error_code or "target_failed"
    elif all(target["status"] in ("ready", "unavailable") for target in targets):
        phase = "ready"
    elif generation.status == "superseded":
        phase, reason = "retrying", "lease_expired"
    else:
        job = session.get(StewardJob, generation.job_id) if generation.job_id is not None else None
        if (
            job is None
            or job.status not in ("leased", "running")
            or job.lease_expires_at is None
            or job.lease_expires_at <= utcnow()
        ):
            phase, reason = "retrying", "lease_expired"
        if worker_stopped(session, space_id=space_id):
            phase, reason = "failed", "worker_stopped"
    inferred_nodes, inferred_edges, revision_extra = steward_overlay.payload_for(
        session,
        account_id=current.id,
        space_id=space_id,
        root_user_id=current.user_id,
        core_nodes=nodes,
    )
    # R2/R3 与 09-13 推测层显示合同的一致口径：无 viewer confirmed 路径的授权成员
    # 保留为 space_member 节点；仅当确有活跃推测边指向该端点时，才按推测层既有
    # 合同改标 inferred_path（保留推测角标/称谓与推测节点摆位）。
    inferred_endpoints = {
        uid
        for edge in inferred_edges
        for uid in (edge["subject_user_id"], edge["object_user_id"], edge["new_user_id"])
        if uid is not None
    }
    if inferred_endpoints:
        nodes = [
            (
                {**node, "inclusion_reason_code": "inferred_path"}
                if int(node["user_id"]) in inferred_endpoints
                and node.get("inclusion_reason_code") == "space_member"
                else node
            )
            for node in nodes
        ]
    payload: dict[str, Any] = {
        "space_id": space_id,
        "status": "current"
        if is_published
        else "failed"
        if generation.status == "failed"
        else "running",
        "view_version": generation.id,
        "computed_at": generation.published_at,
        "nodes": [*nodes, *inferred_nodes],
        "edges": edges,
        "inferred_edges": inferred_edges,
        "topology_edges": list(skeleton.get("topology_edges", [])),
        "truncated": False,
        "next_cursor": None,
        "stale_reason": reason,
    }
    if progressive:
        payload["progress"] = _progress(
            generation=generation.id,
            revision=view.revision + revision_extra,
            phase=phase,
            targets=targets,
            topology=view.topology_revision or "",
            reason=reason,
        )
    return payload, generation.valid_until
