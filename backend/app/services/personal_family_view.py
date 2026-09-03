"""Build and read the server-authoritative PersonalFamilyView projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import PERSONAL_FAMILY_VIEW_NOT_FOUND, raise_api_error
from app.models.account import Account
from app.models.personal_family_view import (
    PersonalFamilyView,
    PersonalFamilyViewEdge,
    PersonalFamilyViewNode,
)
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.services import visibility
from app.services.relationship_graph import load_graph
from app.services.relationship_resolver import resolve_relationship, steps_to_json
from app.utils.timeutil import utcnow

COMPUTATION_VERSION = "pfv-v1"


def _active_space_member(session: Session, *, space_id: int, user_id: int) -> bool:
    return (
        session.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == user_id,
                SpaceMember.status == "active",
            )
        )
        is not None
    )


def _view_for_actor(session: Session, *, account: Account, space_id: int) -> PersonalFamilyView:
    if session.get(FamilySpace, space_id) is None or not _active_space_member(
        session, space_id=space_id, user_id=account.user_id
    ):
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    view = session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == account.id,
            PersonalFamilyView.root_user_id == account.user_id,
            PersonalFamilyView.space_id == space_id,
        )
    )
    if view is None:
        now = utcnow()
        view = PersonalFamilyView(
            viewer_account_id=account.id,
            root_user_id=account.user_id,
            space_id=space_id,
            status="never_computed",
            view_version=0,
            computation_version=COMPUTATION_VERSION,
            created_at=now,
            updated_at=now,
        )
        session.add(view)
        session.flush()
    return view


def _input_hash(graph_hash: str, *, policy_version: str) -> str:
    return hashlib.sha256(
        f"{graph_hash}:{policy_version}:{COMPUTATION_VERSION}".encode()
    ).hexdigest()


def _node_display(
    session: Session,
    actor: User,
    target: User,
    space_id: int,
    *,
    bridge_authorized: bool = False,
) -> tuple[dict[str, Any], str]:
    decision = visibility.evaluate(
        session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
    )
    if bridge_authorized and not decision.visible:
        decision = visibility.VisibilityDecision(
            visibility.LEVEL_LINEAGE_SUMMARY,
            {
                field: visibility.FIELD_CLEAR
                if field in visibility.BASELINE_FIELDS
                else visibility.FIELD_MASKED
                for field in visibility.PROFILE_FIELDS
            },
            visibility.PURPOSE_GRAPH,
        )
    if not decision.visible:
        raise ValueError("invisible node cannot enter PersonalFamilyView")
    return jsonable_encoder(visibility.payload_from_decision(decision, target)), decision.level


def rebuild_view(session: Session, *, account: Account, space_id: int) -> PersonalFamilyView:
    """Rebuild one view from current confirmed graph facts, atomically in caller transaction."""
    view = _view_for_actor(session, account=account, space_id=space_id)
    actor = session.get(User, account.user_id)
    if actor is None:  # pragma: no cover - account FK guarantees this
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    graph = load_graph(session, viewer_user_id=actor.id, space_id=space_id)
    now = utcnow()
    view.status = "running"
    view.updated_at = now
    session.flush()
    session.query(PersonalFamilyViewNode).filter(PersonalFamilyViewNode.view_id == view.id).delete()
    session.query(PersonalFamilyViewEdge).filter(PersonalFamilyViewEdge.view_id == view.id).delete()
    for target_id in sorted(graph.node_genders):
        target = session.get(User, target_id)
        if target is None:
            continue
        if target.id == actor.id:
            resolution = None
        else:
            resolution = resolve_relationship(
                session, viewer_user_id=actor.id, target_user_id=target.id, space_id=space_id
            )
            if not resolution.found:
                continue
        display, level = _node_display(
            session,
            actor,
            target,
            space_id,
            bridge_authorized=target.id in graph.bridge_user_ids,
        )
        session.add(
            PersonalFamilyViewNode(
                view_id=view.id,
                user_id=target.id,
                display_json=display,
                visibility_level=level,
                inclusion_reason_code="root" if target.id == actor.id else "confirmed_path",
                source_fact_ids_json=[],
                authorization_basis_json={"space_id": space_id, "visibility": level},
                policy_version=visibility.PURPOSE_GRAPH,
                computation_version=COMPUTATION_VERSION,
            )
        )
        if resolution is None:
            continue
        main_path = steps_to_json(resolution.main_path)
        alt_paths = [steps_to_json(path) for path in resolution.alt_paths]
        source_ids = sorted(
            {
                step["fact_id"]
                for step in main_path
                if step.get("fact_id") is not None and step["fact_id"] > 0
            }
        )
        session.add(
            PersonalFamilyViewEdge(
                view_id=view.id,
                from_user_id=actor.id,
                to_user_id=target.id,
                edge_kind=main_path[-1]["edge_type"] if main_path else "self",
                source_fact_id=source_ids[0] if source_ids else None,
                path_json=main_path,
                alternative_paths_json=alt_paths,
                path_class=resolution.path_class,
                concept_code=resolution.concept_code,
                term=resolution.explanation_structural,
                inclusion_reason_code="confirmed_path",
                authorization_basis_json={"space_id": space_id, "visibility": level},
                policy_version=visibility.PURPOSE_GRAPH,
                computation_version=COMPUTATION_VERSION,
            )
        )
    view.status = "current"
    view.view_version += 1
    view.input_hash = _input_hash(graph.snapshot_hash, policy_version=visibility.PURPOSE_GRAPH)
    view.policy_version = visibility.PURPOSE_GRAPH
    view.computed_at = now
    view.invalidated_at = None
    view.failed_reason = None
    view.updated_at = now
    session.flush()
    return view


def get_current_view(
    session: Session, *, account: Account, space_id: int
) -> PersonalFamilyView | None:
    """Read an existing projection without materializing or rebuilding it."""
    if session.get(FamilySpace, space_id) is None or not _active_space_member(
        session, space_id=space_id, user_id=account.user_id
    ):
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    return session.scalar(
        select(PersonalFamilyView).where(
            PersonalFamilyView.viewer_account_id == account.id,
            PersonalFamilyView.root_user_id == account.user_id,
            PersonalFamilyView.space_id == space_id,
        )
    )


def get_view(session: Session, *, account: Account, space_id: int) -> PersonalFamilyView:
    """Return a current projection only after rechecking current authorization."""
    view = _view_for_actor(session, account=account, space_id=space_id)
    if view.status == "never_computed":
        return rebuild_view(session, account=account, space_id=space_id)
    return view


def view_payload(session: Session, *, account: Account, space_id: int) -> dict[str, Any]:
    view = get_view(session, account=account, space_id=space_id)
    return _view_payload_for_view(session, account=account, space_id=space_id, view=view)


def current_view_payload(
    session: Session, *, account: Account, space_id: int
) -> dict[str, Any] | None:
    """Read and authorize an existing projection without creating one."""
    view = get_current_view(session, account=account, space_id=space_id)
    if view is None:
        return None
    return _view_payload_for_view(session, account=account, space_id=space_id, view=view)


def _view_payload_for_view(
    session: Session, *, account: Account, space_id: int, view: PersonalFamilyView
) -> dict[str, Any]:
    nodes = session.scalars(
        select(PersonalFamilyViewNode)
        .where(PersonalFamilyViewNode.view_id == view.id)
        .order_by(PersonalFamilyViewNode.user_id)
    ).all()
    edges = session.scalars(
        select(PersonalFamilyViewEdge)
        .where(PersonalFamilyViewEdge.view_id == view.id)
        .order_by(PersonalFamilyViewEdge.to_user_id)
    ).all()
    actor = session.get(User, account.user_id)
    if actor is None:
        raise_api_error(404, PERSONAL_FAMILY_VIEW_NOT_FOUND, "个人家族视图不存在")
    graph = load_graph(session, viewer_user_id=actor.id, space_id=space_id)
    authorized_nodes: list[PersonalFamilyViewNode] = []
    visible_ids: set[int] = set()
    for node in nodes:
        target = session.get(User, node.user_id)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible and node.user_id not in graph.bridge_user_ids:
            continue
        if node.user_id in graph.bridge_user_ids and not decision.visible:
            decision = visibility.VisibilityDecision(
                visibility.LEVEL_LINEAGE_SUMMARY,
                {
                    field: visibility.FIELD_CLEAR
                    if field in visibility.BASELINE_FIELDS
                    else visibility.FIELD_MASKED
                    for field in visibility.PROFILE_FIELDS
                },
                visibility.PURPOSE_GRAPH,
            )
        node.display_json = jsonable_encoder(visibility.payload_from_decision(decision, target))
        node.visibility_level = decision.level
        authorized_nodes.append(node)
        visible_ids.add(node.user_id)
    edges = [
        edge
        for edge in edges
        if edge.from_user_id in visible_ids and edge.to_user_id in visible_ids
    ]
    return {
        "space_id": space_id,
        "status": view.status,
        "view_version": view.view_version,
        "computed_at": view.computed_at,
        "nodes": [
            {
                "user_id": node.user_id,
                "display": node.display_json,
                "visibility_level": node.visibility_level,
                "inclusion_reason_code": node.inclusion_reason_code,
            }
            for node in authorized_nodes
        ],
        "edges": [
            {
                "from_user_id": edge.from_user_id,
                "to_user_id": edge.to_user_id,
                "edge_kind": edge.edge_kind,
                "path": edge.path_json,
                "alternative_paths": edge.alternative_paths_json,
                "path_class": edge.path_class,
                "concept_code": edge.concept_code,
                "term": edge.term,
                "inclusion_reason_code": edge.inclusion_reason_code,
            }
            for edge in edges
        ],
        "truncated": False,
        "next_cursor": None,
        "stale_reason": view.failed_reason if view.status in ("stale", "failed") else None,
    }


def etag_for(view: PersonalFamilyView) -> str:
    return json.dumps([view.id, view.view_version, view.input_hash], separators=(",", ":"))


def invalidate_space_views(session: Session, *, space_id: int) -> int:
    """Mark projections stale; read authorization still gates every response."""
    rows = session.scalars(
        select(PersonalFamilyView).where(
            PersonalFamilyView.space_id == space_id,
            PersonalFamilyView.status != "never_computed",
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.status = "stale"
        row.invalidated_at = now
        row.updated_at = now
    return len(rows)


def rebuild_space_views(session: Session, *, space_id: int) -> int:
    """Rebuild stale projections for a Steward space job."""
    rows = session.scalars(
        select(PersonalFamilyView).where(
            PersonalFamilyView.space_id == space_id,
            PersonalFamilyView.status.in_(("queued", "stale", "failed", "never_computed")),
        )
    ).all()
    rebuilt = 0
    for row in rows:
        account = session.get(Account, row.viewer_account_id)
        if account is None:
            continue
        try:
            rebuild_view(session, account=account, space_id=space_id)
        except Exception as exc:  # keep one malformed projection from blocking the space
            row.status = "failed"
            row.failed_reason = type(exc).__name__
            row.updated_at = utcnow()
        else:
            rebuilt += 1
    return rebuilt


__all__ = [
    "current_view_payload",
    "etag_for",
    "get_current_view",
    "get_view",
    "invalidate_space_views",
    "rebuild_space_views",
    "rebuild_view",
    "view_payload",
]
