"""Read-only query surface for Steward child runs.

This module is deliberately separate from the Assistant query registry.  It only
reads published Steward projections and takes scope from the verified run
identity; caller input can select a target, never an authorization scope.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import raise_api_error
from app.models.account import Account
from app.models.space import SpaceMember
from app.models.steward import (
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardModelCall,
    StewardPublication,
    StewardTermProjection,
    StewardViewTarget,
)
from app.services.agent_execution import StewardExecution

TOOL_GET_SPACE_SNAPSHOT = "familygraph.steward.get_space_snapshot"
TOOL_LIST_SPACE_NODES = "familygraph.steward.list_space_nodes"
TOOL_GET_VIEWER_TARGET = "familygraph.steward.get_viewer_target"
TOOL_GET_VIEWER_TERM = "familygraph.steward.get_viewer_term"
TOOL_GET_EVIDENCE = "familygraph.steward.get_evidence"
TOOL_GET_RELATIONSHIP_PATH = "familygraph.steward.get_relationship_path"

STEWARD_TOOL_NAMES = frozenset(
    {
        TOOL_GET_SPACE_SNAPSHOT,
        TOOL_LIST_SPACE_NODES,
        TOOL_GET_VIEWER_TARGET,
        TOOL_GET_VIEWER_TERM,
        TOOL_GET_EVIDENCE,
        TOOL_GET_RELATIONSHIP_PATH,
    }
)

STEWARD_VIEWER_TOOL_NAMES = frozenset(
    {
        TOOL_GET_VIEWER_TARGET,
        TOOL_GET_VIEWER_TERM,
    }
)
STEWARD_TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    TOOL_GET_SPACE_SNAPSHOT: {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    TOOL_LIST_SPACE_NODES: {
        "type": "object",
        "properties": {
            "cursor": {"type": "string", "maxLength": 32},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": [],
        "additionalProperties": False,
    },
    TOOL_GET_VIEWER_TARGET: {
        "type": "object",
        "properties": {"target_user_id": {"type": "integer", "minimum": 1}},
        "required": ["target_user_id"],
        "additionalProperties": False,
    },
    TOOL_GET_VIEWER_TERM: {
        "type": "object",
        "properties": {
            "root_user_id": {"type": "integer", "minimum": 1},
            "target_user_id": {"type": "integer", "minimum": 1},
        },
        "required": ["root_user_id", "target_user_id"],
        "additionalProperties": False,
    },
    TOOL_GET_EVIDENCE: {
        "type": "object",
        "properties": {
            "target_user_id": {"type": "integer", "minimum": 1},
            "evidence_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "maxItems": 32,
            },
        },
        "required": ["target_user_id"],
        "additionalProperties": False,
    },
    TOOL_GET_RELATIONSHIP_PATH: {
        "type": "object",
        "properties": {
            "from_user_id": {"type": "integer", "minimum": 1},
            "to_user_id": {"type": "integer", "minimum": 1},
        },
        "required": ["from_user_id", "to_user_id"],
        "additionalProperties": False,
    },
}


def execute_steward_tool(
    db: Session,
    *,
    execution: StewardExecution,
    name: str,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute one bounded read using only the already-fenced run scope."""
    if name == TOOL_GET_SPACE_SNAPSHOT:
        return _space_snapshot(db, execution.space_id)
    if name == TOOL_LIST_SPACE_NODES:
        return _list_space_nodes(db, execution.space_id, input_payload)
    if name == TOOL_GET_VIEWER_TARGET:
        return _viewer_target(db, execution, int(input_payload["target_user_id"]))
    if name == TOOL_GET_VIEWER_TERM:
        return _viewer_term(
            db,
            execution,
            root_user_id=int(input_payload["root_user_id"]),
            target_user_id=int(input_payload["target_user_id"]),
        )
    if name == TOOL_GET_EVIDENCE:
        evidence_ids = input_payload.get("evidence_ids")
        return _evidence(
            db,
            execution,
            target_user_id=int(input_payload["target_user_id"]),
            evidence_ids=None if evidence_ids is None else {int(value) for value in evidence_ids},
        )
    if name == TOOL_GET_RELATIONSHIP_PATH:
        return _relationship_path(
            db,
            execution,
            from_user_id=int(input_payload["from_user_id"]),
            to_user_id=int(input_payload["to_user_id"]),
        )
    raise_api_error(404, "AGENT_TOOL_UNKNOWN", "未知 Steward 工具")


def _published_generation(db: Session, space_id: int) -> StewardGeneration | None:
    publication = db.get(StewardPublication, space_id)
    if publication is None:
        return None
    generation = db.get(StewardGeneration, publication.generation_id)
    if generation is None or generation.status != "published":
        return None
    return generation


def _safe_stat(stats: dict[str, Any], key: str) -> int | None:
    value = stats.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _space_snapshot(db: Session, space_id: int) -> dict[str, Any]:
    generation = _published_generation(db, space_id)
    if generation is None:
        return {"available": False, "reason": "not_published"}
    job = db.get(StewardJob, generation.job_id) if generation.job_id is not None else None
    return {
        "available": True,
        "space_id": space_id,
        "generation_id": generation.id,
        "revision": generation.execution_cursor,
        "published_at": generation.published_at,
        "valid_until": generation.valid_until,
        "required_views": generation.required_views,
        "ready_views": generation.ready_views,
        "failed_views": generation.failed_views,
        "node_count": _safe_stat(generation.stats_json, "node_count"),
        "evidence_count": _safe_stat(generation.stats_json, "evidence_count"),
        "policy_version": job.policy_version if job is not None else None,
    }


def _list_space_nodes(db: Session, space_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    generation = _published_generation(db, space_id)
    if generation is None:
        return {"available": False, "reason": "not_published", "nodes": []}
    view = db.scalar(
        select(StewardGenerationView)
        .where(
            StewardGenerationView.generation_id == generation.id,
            StewardGenerationView.space_id == space_id,
            StewardGenerationView.status == "ready",
        )
        .order_by(StewardGenerationView.id)
    )
    if view is None:
        return {"available": False, "reason": "not_published", "nodes": []}
    limit = int(payload.get("limit", 50))
    raw_cursor = payload.get("cursor", "0") or "0"
    try:
        cursor = int(raw_cursor)
    except (TypeError, ValueError):
        raise_api_error(422, "AGENT_TOOL_SCHEMA_INVALID", "cursor 必须是非负整数")
    if cursor < 0:
        raise_api_error(422, "AGENT_TOOL_SCHEMA_INVALID", "cursor 必须是非负整数")
    node_ids = sorted(
        {
            int(node["user_id"])
            for node in (view.skeleton_json.get("nodes", []) or [])
            if isinstance(node, dict)
            and isinstance(node.get("user_id"), int)
            and not isinstance(node.get("user_id"), bool)
        }
    )
    page = node_ids[cursor : cursor + limit]
    return {
        "available": True,
        "revision": generation.execution_cursor,
        "nodes": [
            {"code": f"node-{user_id}", "user_id": user_id, "available": True} for user_id in page
        ],
        "next_cursor": str(cursor + limit) if cursor + limit < len(node_ids) else None,
    }


def _viewer_view(db: Session, execution: StewardExecution) -> StewardGenerationView | None:
    if execution.viewer_account_id is None:
        return None
    account = db.get(Account, execution.viewer_account_id)
    if account is None:
        return None
    member = db.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == execution.space_id,
            SpaceMember.user_id == account.user_id,
            SpaceMember.status == "active",
        )
    )
    if member is None:
        return None
    generation = _published_generation(db, execution.space_id)
    if generation is None:
        return None
    return db.scalar(
        select(StewardGenerationView).where(
            StewardGenerationView.generation_id == generation.id,
            StewardGenerationView.space_id == execution.space_id,
            StewardGenerationView.viewer_account_id == execution.viewer_account_id,
            StewardGenerationView.root_user_id == account.user_id,
            StewardGenerationView.status == "ready",
        )
    )


def _unavailable() -> dict[str, Any]:
    return {"available": False, "reason": "not_available"}


def _require_viewer_view(db: Session, execution: StewardExecution) -> StewardGenerationView:
    view = _viewer_view(db, execution)
    if view is None:
        raise_api_error(
            403,
            "STEWARD_VIEWER_SCOPE_UNAVAILABLE",
            "当前 Steward run 没有可用的 viewer scope",
        )
    return view


def _viewer_target_row(
    db: Session, execution: StewardExecution, target_user_id: int
) -> StewardViewTarget | None:
    view = _viewer_view(db, execution)
    if view is None:
        return None
    return db.scalar(
        select(StewardViewTarget).where(
            StewardViewTarget.view_id == (view.result_view_id or view.id),
            StewardViewTarget.target_user_id == target_user_id,
        )
    )


def _viewer_target(db: Session, execution: StewardExecution, target_user_id: int) -> dict[str, Any]:
    view = _require_viewer_view(db, execution)
    target = db.scalar(
        select(StewardViewTarget).where(
            StewardViewTarget.view_id == (view.result_view_id or view.id),
            StewardViewTarget.target_user_id == target_user_id,
        )
    )
    if target is None or target.status != "ready" or target.edge_json is None:
        return _unavailable()
    edge = dict(target.edge_json)
    return {
        "available": True,
        "target_user_id": target_user_id,
        "status": target.status,
        "revision": view.revision,
        "distance": target.distance,
        "path_class": edge.get("path_class"),
        "term_status": "available" if edge.get("term") else "unavailable",
        "reason_code": target.reason_code,
    }


def _viewer_term(
    db: Session,
    execution: StewardExecution,
    *,
    root_user_id: int,
    target_user_id: int,
) -> dict[str, Any]:
    _require_viewer_view(db, execution)
    account = db.get(Account, execution.viewer_account_id)
    if account is None or account.user_id != root_user_id:
        return _unavailable()
    projection = db.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.space_id == execution.space_id,
            StewardTermProjection.viewer_account_id == execution.viewer_account_id,
            StewardTermProjection.root_user_id == root_user_id,
            StewardTermProjection.target_user_id == target_user_id,
        )
    )
    if projection is None or projection.status not in {"active", "unchanged"}:
        return _unavailable()
    return {
        "available": True,
        "target_user_id": target_user_id,
        "term": projection.term,
        "source": projection.origin or projection.baseline_source,
        "status": projection.status,
        "revision": projection.revision,
        "reason_code": projection.last_attempt_status,
    }


def _published_target(
    db: Session, execution: StewardExecution, target_user_id: int
) -> StewardViewTarget | None:
    generation = _published_generation(db, execution.space_id)
    if generation is None:
        return None
    return db.scalar(
        select(StewardViewTarget)
        .join(
            StewardGenerationView,
            StewardGenerationView.id == StewardViewTarget.view_id,
        )
        .where(
            StewardGenerationView.generation_id == generation.id,
            StewardGenerationView.space_id == execution.space_id,
            StewardGenerationView.status == "ready",
            StewardViewTarget.target_user_id == target_user_id,
        )
        .order_by(StewardViewTarget.id)
    )


def _evidence(
    db: Session,
    execution: StewardExecution,
    *,
    target_user_id: int,
    evidence_ids: set[int] | None,
) -> dict[str, Any]:
    # The attempt must still be the live child-run execution unit.  This keeps
    # evidence reads tied to the run that was fenced by the caller.
    attempt = db.scalar(
        select(StewardModelCall).where(
            StewardModelCall.run_id == execution.run_id,
            StewardModelCall.space_id == execution.space_id,
            StewardModelCall.attempt_no == execution.expected_attempt,
            StewardModelCall.status == "in_flight",
        )
    )
    generation = _published_generation(db, execution.space_id)
    if (
        attempt is None
        or generation is None
        or generation.job_id != attempt.job_id
        or (execution.steward_attempt_id is not None and attempt.id != execution.steward_attempt_id)
    ):
        return _unavailable()
    target = (
        _viewer_target_row(db, execution, target_user_id)
        if execution.viewer_account_id is not None
        else _published_target(db, execution, target_user_id)
    )
    if target is None or target.status != "ready" or target.edge_json is None:
        return _unavailable()
    edge = dict(target.edge_json)
    evidence: list[dict[str, Any]] = []
    for step in edge.get("path", []):
        evidence_id = step.get("relation_id") or step.get("fact_id")
        if not isinstance(evidence_id, int) or (
            evidence_ids is not None and evidence_id not in evidence_ids
        ):
            continue
        evidence.append(
            {
                "evidence_id": evidence_id,
                "kind": "relation" if step.get("relation_id") is not None else "fact",
                "direction": step.get("direction"),
                "revision": generation.execution_cursor,
            }
        )
    return {"available": True, "target_user_id": target_user_id, "evidence": evidence}


def _published_view_for_root(
    db: Session, execution: StewardExecution, root_user_id: int
) -> StewardGenerationView | None:
    generation = _published_generation(db, execution.space_id)
    if generation is None:
        return None
    accounts = db.scalars(select(Account).where(Account.user_id == root_user_id)).all()
    for account in accounts:
        member = db.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == execution.space_id,
                SpaceMember.user_id == root_user_id,
                SpaceMember.status == "active",
            )
        )
        if member is None:
            continue
        view = db.scalar(
            select(StewardGenerationView).where(
                StewardGenerationView.generation_id == generation.id,
                StewardGenerationView.space_id == execution.space_id,
                StewardGenerationView.viewer_account_id == account.id,
                StewardGenerationView.root_user_id == root_user_id,
                StewardGenerationView.status == "ready",
            )
        )
        if view is not None:
            return view
    return None


def _relationship_path(
    db: Session,
    execution: StewardExecution,
    *,
    from_user_id: int,
    to_user_id: int,
) -> dict[str, Any]:
    # With a viewer claim, the requested root must be that viewer.  Without a
    # viewer claim, resolve only a published view rooted at the requested user;
    # no account or hidden graph row is disclosed when it cannot be proven.
    if execution.viewer_account_id is not None:
        account = db.get(Account, execution.viewer_account_id)
        if account is None or account.user_id != from_user_id:
            return _unavailable()
        view = _viewer_view(db, execution)
    else:
        view = _published_view_for_root(db, execution, from_user_id)
    if view is None:
        return _unavailable()
    target = db.scalar(
        select(StewardViewTarget).where(
            StewardViewTarget.view_id == (view.result_view_id or view.id),
            StewardViewTarget.target_user_id == to_user_id,
        )
    )
    if target is None or target.status != "ready" or target.edge_json is None:
        return _unavailable()
    edge = dict(target.edge_json)
    return {
        "available": True,
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "path_class": edge.get("path_class"),
        "path": edge.get("path", []),
        "alternative_paths": edge.get("alternative_paths", []),
        "algorithm_revision": view.topology_revision,
    }
