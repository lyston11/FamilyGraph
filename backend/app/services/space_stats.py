"""空间限定统计：由当前授权 PersonalFamilyView 投影服务端聚合（design.md §4）。

口径：
- 节点/边/成员只在当前授权复核通过后计数；`none` 节点与不可见边/成员完全
  不计入，也不能从计数反推存在；
- relation_distribution 仅按允许 dir_class 聚合，不携带人物 ID 或阻断原因；
- pending_action_cards 只统计当前账号在该空间的 pending ActionCard；
  pending_memberships 按既有成员列表可见性口径统计空间 pending 行；
- 视图非 current 时显式返回状态与 stale 原因；快照行在读取时逐个重新授权，
  撤权后旧快照内容立即不可计（授权失败 → 安全 404，而非回退旧统计）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.personal_family_view import PersonalFamilyViewEdge, PersonalFamilyViewNode
from app.models.space import SpaceMember
from app.models.steward import ActionCard
from app.models.user import User
from app.services import personal_family_view, visibility
from app.services.family_projection import authorized_space_or_404

STATE_PENDING = "pending"

_NO_STALE_REASON = "projection_not_current"


def _dir_class_for_path(path: list[dict[str, Any]]) -> str:
    """主路径 → dir_class：代数差（上辈步 − 下辈步）判长幼，同代含配偶归 spouse。"""
    up = sum(
        1 for step in path if step.get("edge_type") == "parent" and step.get("direction") == "up"
    )
    down = sum(
        1 for step in path if step.get("edge_type") == "parent" and step.get("direction") == "down"
    )
    has_spouse = any(step.get("edge_type") in ("spouse", "partner") for step in path)
    net = up - down
    if net > 0:
        return "elder"
    if net < 0:
        return "younger"
    return "spouse" if has_spouse else "peer"


def space_stats_payload(session: Session, *, account: Account, space_id: int) -> dict[str, Any]:
    """构造空间统计聚合；调用方（API 层）负责 ETag/304。"""
    space, actor = authorized_space_or_404(session, account=account, space_id=space_id)
    view = personal_family_view.get_view(session, account=account, space_id=space_id)
    # 首读物化（09-11 R5：仅显式提交的首次物化；已存在的 stale/queued 行不再
    # 隐式重算，失效由 domain_events 驱动、steward 重建）。
    if view.status == "never_computed":
        personal_family_view.rebuild_view(session, account=account, space_id=space_id)
    # 首读重建落库（可重建投影；失效由 domain_events 驱动、steward 重建），
    # 使 view_version/computed_at/stale 聚合稳定，条件请求（304）可复用。
    session.commit()
    bridge_ids = _bridge_authorized_ids(session, actor, space_id)

    # 逐节点重新执行当前字段级 VisibilityPolicy：撤权/收紧后旧快照行立即出局
    nodes = session.scalars(
        select(PersonalFamilyViewNode).where(PersonalFamilyViewNode.view_id == view.id)
    ).all()
    visible_ids: set[int] = set()
    for node in nodes:
        target = session.get(User, node.user_id)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible and node.user_id not in bridge_ids:
            continue
        visible_ids.add(node.user_id)

    edges = session.scalars(
        select(PersonalFamilyViewEdge).where(PersonalFamilyViewEdge.view_id == view.id)
    ).all()
    distribution: dict[str, int] = {}
    edge_count = 0
    for edge in edges:
        if edge.from_user_id not in visible_ids or edge.to_user_id not in visible_ids:
            continue
        edge_count += 1
        dir_class = _dir_class_for_path(edge.path_json or [])
        distribution[dir_class] = distribution.get(dir_class, 0) + 1

    # 成员投影计数：当前空间 active 成员中对 viewer 可见者（viewer 自身恒计入）
    member_count = 0
    member_rows = session.scalars(
        select(SpaceMember.user_id).where(
            SpaceMember.space_id == space_id, SpaceMember.status == "active"
        )
    ).all()
    for member_user_id in member_rows:
        member = session.get(User, member_user_id)
        if member is None:
            continue
        decision = visibility.evaluate(
            session, actor, member, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if decision.visible:
            member_count += 1

    pending_cards = (
        session.query(ActionCard.id)
        .filter(
            ActionCard.space_id == space_id,
            ActionCard.recipient_account_id == account.id,
            ActionCard.state == STATE_PENDING,
        )
        .count()
    )
    # 与既有 GET /spaces/{id}/members 同一可见性口径：active 成员可见空间 pending 行
    pending_memberships = (
        session.query(SpaceMember.id)
        .filter(SpaceMember.space_id == space_id, SpaceMember.status == "pending")
        .count()
    )

    return {
        "space_id": space_id,
        "space_kind": space.kind,
        "status": view.status,
        "view_version": view.view_version,
        "node_count": len(visible_ids),
        "edge_count": edge_count,
        "member_count": member_count,
        "relation_distribution": [
            {"dir_class": dir_class, "count": count}
            for dir_class, count in sorted(distribution.items())
        ],
        "pending_action_cards": pending_cards,
        "pending_memberships": pending_memberships,
        "computed_at": view.computed_at,
        # 非 current 必须显式标因：优先 FSM 失败原因，否则给出稳定的占位原因
        "stale_reason": (
            (view.failed_reason or _NO_STALE_REASON) if view.status != "current" else None
        ),
    }


def _bridge_authorized_ids(session: Session, actor: User, space_id: int) -> set[int]:
    """当前 anchor 可达的 active bridge 对端（与 load_graph 同一授权基础）。"""
    from app.services.relationship_graph import load_graph

    return set(load_graph(session, viewer_user_id=actor.id, space_id=space_id).bridge_user_ids)


__all__ = ["space_stats_payload"]
