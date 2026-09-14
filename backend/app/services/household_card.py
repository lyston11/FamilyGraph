"""Household card：household 空间的服务端授权最小投影（design.md §3）。

数据边界：
- 只消费空间元数据、当前 PersonalFamilyView 版本和 active 成员的安全
  display 投影（visibility.payload_from_decision，与 PersonalFamilyView
  同一口径）；成员行逐人执行当前字段级 VisibilityPolicy 复核。
- 不返回 lineage 节点数组、隐藏成员数量、关系图全量边、不可见目标 ID、
  Memory/Session、附件或未授权字段；`none` 成员完全省略。
- 成员资格撤回/空间删除后，下一次读取在授权复核处安全 404，旧投影不再
  可读；授权失败优先于任何 ETag 命中。
"""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.space import SpaceMember
from app.models.user import User
from app.services import personal_family_view, visibility
from app.services.family_projection import authorized_household_space_or_404

LABEL_ADMIN = "管理员"
LABEL_MEMBER = "成员"
EMPTY_STATE_HINT = "这个家庭还没有其他确认成员，邀请家人加入吧"


def household_card_payload(session: Session, *, account: Account, space_id: int) -> dict[str, Any]:
    """构造 household card 最小投影；调用方（API 层）负责 ETag/304。"""
    space, actor = authorized_household_space_or_404(session, account=account, space_id=space_id)
    # Member display is a current authorized source projection and does not
    # wait for kinship computation. Metadata follows the published view only.
    payload = personal_family_view.current_view_payload(session, account=account, space_id=space_id)

    # viewer 本人恒可见（evaluate self → self_private）
    viewer_decision = visibility.evaluate(
        session, actor, actor, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
    )

    members: list[dict[str, Any]] = []
    member_rows = session.scalars(
        select(SpaceMember)
        .where(SpaceMember.space_id == space_id, SpaceMember.status == "active")
        .order_by(SpaceMember.user_id)
    ).all()
    for row in member_rows:
        if row.user_id == actor.id:
            # viewer 单独投影，成员列表只含「其他确认成员」
            continue
        target = session.get(User, row.user_id)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, actor, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible:
            continue  # `none` 成员完全省略，不生成占位
        members.append(
            {
                "user_id": row.user_id,
                "display": jsonable_encoder(visibility.payload_from_decision(decision, target)),
                "household_label": LABEL_ADMIN if row.role == "space_admin" else LABEL_MEMBER,
                "visibility_level": decision.level,
            }
        )

    return {
        "space_id": space_id,
        "space_kind": "household",
        "space_name": space.name,
        "view_version": payload["view_version"] if payload is not None else 0,
        "computed_at": payload["computed_at"] if payload is not None else None,
        "viewer": jsonable_encoder(visibility.payload_from_decision(viewer_decision, actor)),
        "members": members,
        "allowed_actions": {
            # 邀请是 active 成员权限（commands.spaces._require_inviter），
            # 创建共同家庭要求 identity_confirmed（commands.spaces.create_shared_household）
            "can_invite_members": True,
            "can_create_household": actor.profile_status == "identity_confirmed",
            "empty_state_hint": EMPTY_STATE_HINT if not members else None,
        },
    }


__all__ = ["household_card_payload"]
