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
    # Canonical reads validate the publication and its input/permission fence.
    # An unpublished or stale projection contributes no relationship counts;
    # a GET must never compute or materialize a replacement under a writer.
    payload = personal_family_view.current_view_payload(session, account=account, space_id=space_id)
    status = payload["status"] if payload is not None else "never_computed"
    visible_ids = {int(node["user_id"]) for node in payload["nodes"]} if payload else set()
    edges = payload["edges"] if payload else []
    distribution: dict[str, int] = {}
    edge_count = 0
    for edge in edges:
        if edge["from_user_id"] not in visible_ids or edge["to_user_id"] not in visible_ids:
            continue
        edge_count += 1
        dir_class = _dir_class_for_path(edge.get("path") or [])
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
        "status": status,
        "view_version": payload["view_version"] if payload is not None else 0,
        "node_count": len(visible_ids),
        "edge_count": edge_count,
        "member_count": member_count,
        "relation_distribution": [
            {"dir_class": dir_class, "count": count}
            for dir_class, count in sorted(distribution.items())
        ],
        "pending_action_cards": pending_cards,
        "pending_memberships": pending_memberships,
        "computed_at": payload["computed_at"] if payload is not None else None,
        # 非 current 必须显式标因：优先 FSM 失败原因，否则给出稳定的占位原因
        "stale_reason": (
            ((payload.get("stale_reason") if payload else None) or _NO_STALE_REASON)
            if status != "current"
            else None
        ),
    }


__all__ = ["space_stats_payload"]
