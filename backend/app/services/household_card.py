"""Household card：household 空间的服务端授权最小投影（design.md §3）。

数据边界：
- 只消费空间元数据、当前 PersonalFamilyView 版本和 active 成员的安全
  display 投影（visibility.payload_from_decision，与 PersonalFamilyView
  同一口径）；成员行逐人执行当前字段级 VisibilityPolicy 复核。
- 成员的关系称谓取自**同一份已授权 PFV 投影**（`viewer` 锚定的 edge term，
  见 `relationship-intelligence.md` 的黄金合同）。不新增图加载、不新增授权
  判定，避免第二套可见性口径；解析不到路径的成员为 `null`，不生成占位
  （不泄露「是否存在关系」）。
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


def _relation_terms_by_member(
    payload: dict[str, Any] | None, *, viewer_user_id: int
) -> dict[int, str | None]:
    """从已授权的 PFV 投影取 viewer 视角的成员关系称谓。

    数据源就是调用方已取得的 ``current_view_payload``：其 `edges` 是该 viewer
    已授权的 viewer 锚定关系投影（经 `visibility` 与图谱口径，与大哥片树同源），
    所以这里不新增图加载、不新增授权判定。

    主方向是 `from_user_id == viewer`（实测 PFV edge 为 viewer 锚定）；同时防御性
    处理反向，不假设单一方向。缺失/无路径一律不写条目，消费方得到 `None`。
    """
    terms: dict[int, str | None] = {}
    if payload is None:
        return terms
    for edge in payload.get("edges") or []:
        from_id = edge.get("from_user_id")
        to_id = edge.get("to_user_id")
        if from_id == viewer_user_id and isinstance(to_id, int):
            terms[to_id] = edge.get("term")
        elif to_id == viewer_user_id and isinstance(from_id, int):
            terms[from_id] = edge.get("term")
    return terms


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
    relation_terms = _relation_terms_by_member(payload, viewer_user_id=actor.id)
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
                "relation_term": relation_terms.get(row.user_id),
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
