"""可见性单点（m2a）：授权矩阵的唯一实现（architecture.md §6，QU1=B + AD-9）。

所有跨用户档案数据的 view 层级判定必须经过本模块；custody.py 只管 edit/delete。

层级：
    full     本人 / 同一 active 空间成员 / 直系结构边对端 / 代管创建者 / 管理员
    summary  clan 连通可达——基线字段 + 归属者披露开关已开放类别（AD-9）；
             另含 pending 请求两端点互见（不传递）
    invisible 其余（路由层转 404，防枚举）
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.relation import Relation
from app.models.space import SpaceMember
from app.models.user import DISCLOSURE_KEYS

if TYPE_CHECKING:
    from app.models.user import User

FULL = "full"
SUMMARY = "summary"
INVISIBLE = "invisible"

MASKED: dict[str, bool] = {"__masked__": True}

_BASELINE_FIELDS = ("id", "name")
_DISCLOSURE_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "avatar": ("avatar_path",),
    "dates": ("birth", "death"),
    "bio": ("bio",),
    "photos": (),  # m3a 相册消费
    "attachments": (),  # m3a 链接附件消费
}
_FULL_FIELDS = (
    "id",
    "name",
    "gender",
    "birth",
    "death",
    "bio",
    "avatar_path",
    "privacy_mode",
    "claim_status",
    "created_by",
    "created_at",
)


def _active_edges_between(session: Session, a: int, b: int) -> list[Relation]:
    return list(
        session.scalars(
            select(Relation).where(
                Relation.status == "active",
                or_(
                    (Relation.from_user == a) & (Relation.to_user == b),
                    (Relation.from_user == b) & (Relation.to_user == a),
                ),
            )
        )
    )


def direct_structural_edge(session: Session, a: int, b: int) -> Relation | None:
    """直系结构边：elder/younger/spouse 任方向 active；peer 不算直系。"""
    for edge in _active_edges_between(session, a, b):
        if edge.dir_class in ("elder", "younger", "spouse"):
            return edge
    return None


def shared_active_space(session: Session, a: int, b: int) -> bool:
    counts: dict[int, int] = {}
    for space_id in session.scalars(
        select(SpaceMember.space_id).where(
            SpaceMember.user_id.in_((a, b)),
            SpaceMember.status == "active",
        )
    ):
        counts[space_id] = counts.get(space_id, 0) + 1
    return any(v >= 2 for v in counts.values())


def reachable_ids(session: Session, viewer_id: int) -> set[int]:
    """viewer 沿活动边无向 BFS 的连通分量（含 viewer）。pending 边不传递。"""
    edges = session.scalars(select(Relation).where(Relation.status == "active")).all()
    adj: dict[int, set[int]] = {}
    for e in edges:
        adj.setdefault(e.from_user, set()).add(e.to_user)
        adj.setdefault(e.to_user, set()).add(e.from_user)
    seen = {viewer_id}
    stack = [viewer_id]
    while stack:
        cur = stack.pop()
        for nb in adj.get(cur, ()):  # noqa: B007
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return seen


def _pending_pair_edge(session: Session, a: int, b: int) -> Relation | None:
    """pending 边：仅授予两端点互见摘要（AD-4 通知需要），不做传递可达。"""
    return session.scalar(
        select(Relation).where(
            Relation.status == "pending",
            or_(
                (Relation.from_user == a) & (Relation.to_user == b),
                (Relation.from_user == b) & (Relation.to_user == a),
            ),
        )
    )


def classify(session: Session, viewer: User, target: User) -> str:
    """层级判定（QU1=B + AD-9）。

    除矩阵三来源外含两条 D5/A4 派生规则：
      - 代管创建者（target.created_by == viewer）：完整视图（编辑权仍由 custody 裁定）
      - 管理员：数据兜底修正需要完整视图（操作走 audit）
    """
    if viewer.id == target.id or viewer.is_admin:
        return FULL
    if target.created_by == viewer.id:
        return FULL  # 代管创建者保有查看权；handover 已认领后编辑权由 custody 收走
    if shared_active_space(session, viewer.id, target.id):
        return FULL
    if direct_structural_edge(session, viewer.id, target.id) is not None:
        return FULL
    if target.id in reachable_ids(session, viewer.id):
        return SUMMARY
    if _pending_pair_edge(session, viewer.id, target.id) is not None:
        return SUMMARY
    return INVISIBLE


def _disclosure_flags(target: User) -> dict[str, bool]:
    raw: Any
    if isinstance(target.clan_disclosure_json, str):
        try:
            raw = json.loads(target.clan_disclosure_json)
        except Exception:  # noqa: BLE001
            raw = {}
    else:
        raw = target.clan_disclosure_json or {}
    return {k: bool(raw.get(k, False)) for k in DISCLOSURE_KEYS}


def user_payload_for(session: Session, viewer: User, target: User) -> dict[str, Any] | None:
    """按矩阵生成对外用户载荷；invisible → None。

    summary 级：基线 {id,name} + 已披露类别真实值 + 未披露敏感类别 MASKED；
    操作元数据（privacy_mode/claim_status/created_by/created_at）原样透出。
    """
    level = classify(session, viewer, target)
    if level == INVISIBLE:
        return None

    full_data: dict[str, Any] = {field: getattr(target, field) for field in _FULL_FIELDS}
    if level == FULL:
        return full_data

    flags = _disclosure_flags(target)
    sensitive = {"avatar_path", "birth", "death", "bio"}
    disclosed_fields: set[str] = set()
    for category, fields in _DISCLOSURE_FIELD_MAP.items():
        if flags.get(category):
            disclosed_fields.update(fields)

    out: dict[str, Any] = {}
    for field in _FULL_FIELDS:
        if field in sensitive and field not in disclosed_fields:
            out[field] = dict(MASKED)
        else:
            out[field] = full_data[field]
    return out


def visible_member_rows(
    session: Session, viewer: User, candidate_users: list[User]
) -> list[tuple[User, str]]:
    """批量分级（图查询用）：[(user, level)]，invisible 已剔除。"""
    reach = reachable_ids(session, viewer.id)
    out: list[tuple[User, str]] = []
    for user in candidate_users:
        if user.id == viewer.id or user.id in reach:
            out.append((user, classify(session, viewer, user)))
    return out


__all__ = [
    "FULL",
    "INVISIBLE",
    "MASKED",
    "SUMMARY",
    "classify",
    "direct_structural_edge",
    "reachable_ids",
    "shared_active_space",
    "user_payload_for",
    "visible_member_rows",
]
