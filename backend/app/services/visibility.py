"""可见性单点 v2（spec/architecture.md §0.1）：四级层级 + 字段级 mask。

所有跨用户档案数据的投影判定必须经过本模块；custody.py 只管 edit/delete。

层级（优先序自高到低）：
    self_private       本人
    household_detail   同 household 空间双方 active 且均非 guest；代管创建者映射此层
    lineage_summary    同 lineage 空间 active / space_profile_refs 最小引用 /
                       直系结构边（跨 household 不再自动 full）/ pending 最小互见
    none               其余（路由层转 404，防枚举）

规则：
- purpose 只能收紧不得放宽：agent/rag/search/statistics 投影不超过 profile 口径。
- 披露偏好只扩展字段投影，不单独授予可见性；逐空间覆盖全局，默认不公开。
- 未成年人 overlay 最后收紧：精确生卒/简介等对任何非本人主体遮蔽。
- platform_operator 不进入优先链（等同无关用户）；break-glass 属未来独立审计接口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.models.user import User
from app.models.v2_foundation import PlatformRoleAssignment
from app.services.disclosure import disclosed_categories

# ---- 层级 ----
LEVEL_SELF_PRIVATE = "self_private"
LEVEL_HOUSEHOLD_DETAIL = "household_detail"
LEVEL_LINEAGE_SUMMARY = "lineage_summary"
LEVEL_NONE = "none"

_LEVEL_ORDER = {
    LEVEL_NONE: 0,
    LEVEL_LINEAGE_SUMMARY: 1,
    LEVEL_HOUSEHOLD_DETAIL: 2,
    LEVEL_SELF_PRIVATE: 3,
}

# ---- 用途 ----
PURPOSE_PROFILE = "profile"
PURPOSE_GRAPH = "graph"
PURPOSE_SEARCH = "search"
PURPOSE_STATISTICS = "statistics"
PURPOSE_EXPORT = "export"
PURPOSE_AGENT = "agent"
PURPOSE_RAG = "rag"
ALL_PURPOSES = (
    PURPOSE_PROFILE,
    PURPOSE_GRAPH,
    PURPOSE_SEARCH,
    PURPOSE_STATISTICS,
    PURPOSE_EXPORT,
    PURPOSE_AGENT,
    PURPOSE_RAG,
)

# purpose 级别上限：agent/rag/search/statistics 不得超过 profile API 的家庭口径（§0.1）
_PURPOSE_LEVEL_CAP = {
    PURPOSE_PROFILE: LEVEL_HOUSEHOLD_DETAIL,
    PURPOSE_GRAPH: LEVEL_HOUSEHOLD_DETAIL,
    PURPOSE_EXPORT: LEVEL_HOUSEHOLD_DETAIL,
    PURPOSE_SEARCH: LEVEL_LINEAGE_SUMMARY,
    PURPOSE_STATISTICS: LEVEL_LINEAGE_SUMMARY,
    PURPOSE_AGENT: LEVEL_LINEAGE_SUMMARY,
    PURPOSE_RAG: LEVEL_LINEAGE_SUMMARY,
}

# ---- 字段级 mask ----
FIELD_CLEAR = "clear"
FIELD_MASKED = "masked"

MASKED: dict[str, bool] = {"__masked__": True}  # 载荷遮罩哨兵（v1 兼容形状）

BASELINE_FIELDS = ("id", "name")
# 受层级/披露控制的内容字段
CONTENT_FIELDS = ("gender", "birth", "death", "bio", "avatar_path")
# 运维元数据：任何非 none 层级恒明文（非敏感）
META_FIELDS = ("privacy_mode", "claim_status", "created_by", "created_at")
PROFILE_FIELDS = BASELINE_FIELDS + CONTENT_FIELDS + META_FIELDS

# 披露类别 → 内容字段映射（高敏感类对应未来档案列，当前为空占位）
CATEGORY_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "avatar": ("avatar_path",),
    "photos": (),
    "dates": ("birth", "death"),
    "bio": ("bio",),
    "attachments": (),
    "health": (),
    "address": (),
    "school": (),
    "contact": (),
    "private_notes": (),
}

# 高敏感类别：不因 lineage/household/Agent/operator 身份自动开放（§0.1）
HIGH_RISK_CATEGORIES = ("health", "address", "school", "contact", "private_notes")

# 未成年人 overlay：默认最小披露，对任何非本人主体遮蔽
MINOR_OVERLAY_FIELDS = ("birth", "death", "bio")

_MINOR_YEAR_THRESHOLD = 18


@dataclass(frozen=True)
class VisibilityDecision:
    """evaluate 输出：层级 + 字段级 mask + 用途。"""

    level: str
    fields: dict[str, str]
    purpose: str

    @property
    def visible(self) -> bool:
        return self.level != LEVEL_NONE


def _is_platform_operator(session: Session, actor: User) -> bool:
    """平台角色永远不参与家庭可见性判定（architecture §0.1/§0.2）。

    The account role is deliberately loaded from the server-side assignment table rather
    than a JWT compatibility claim or ``users.is_admin`` projection.  A platform
    operator may accidentally have a membership row; that combination must still
    fail closed for every ordinary family-data purpose.
    """
    account_id = getattr(actor.account, "id", None)
    if account_id is None:
        return False
    return (
        session.scalar(
            select(PlatformRoleAssignment.id).where(
                PlatformRoleAssignment.account_id == account_id,
                PlatformRoleAssignment.role == "platform_operator",
            )
        )
        is not None
    )


def direct_structural_edge(session: Session, a: int, b: int) -> Relation | None:
    """直系结构边：elder/younger/spouse 任方向 active；peer 不算直系。"""
    return session.scalar(
        select(Relation).where(
            Relation.status == "active",
            Relation.dir_class.in_(("elder", "younger", "spouse")),
            or_(
                (Relation.from_user == a) & (Relation.to_user == b),
                (Relation.from_user == b) & (Relation.to_user == a),
            ),
        )
    )


def _pending_pair_edge(session: Session, a: int, b: int) -> Relation | None:
    """pending 关系边：仅授予两端点最小互见，不做传递可达。"""
    return session.scalar(
        select(Relation).where(
            Relation.status == "pending",
            or_(
                (Relation.from_user == a) & (Relation.to_user == b),
                (Relation.from_user == b) & (Relation.to_user == a),
            ),
        )
    )


def _active_memberships(
    session: Session, user_id: int, space_context: int | None
) -> dict[int, SpaceMember]:
    stmt = select(SpaceMember).where(SpaceMember.user_id == user_id, SpaceMember.status == "active")
    if space_context is not None:
        stmt = stmt.where(SpaceMember.space_id == space_context)
    return {m.space_id: m for m in session.scalars(stmt).all()}


def _shared_space_kinds(
    session: Session,
    actor_id: int,
    target_id: int,
    space_context: int | None,
) -> tuple[bool, bool]:
    """返回 (可给 household_detail, 可给 lineage_summary)。

    共同空间双方 active 为前提；household 且双方均非 guest → household_detail；
    lineage 空间，或 household 中涉及 guest（guest 不获得 household_detail）
    → 仅 lineage_summary 最小互见。
    """
    mine = _active_memberships(session, actor_id, space_context)
    theirs = _active_memberships(session, target_id, space_context)
    shared = set(mine) & set(theirs)
    household = lineage = False
    for space_id in shared:
        kinds = session.scalar(select(FamilySpace.kind).where(FamilySpace.id == space_id))
        guest_involved = "guest" in (mine[space_id].role, theirs[space_id].role)
        if kinds == "household" and not guest_involved:
            household = True
        else:
            lineage = True
    return household, lineage


def _ref_link(session: Session, actor_id: int, target_id: int, space_context: int | None) -> bool:
    """target 以 active space_profile_ref 出现在 actor 为 active 成员的空间中。"""
    actor_space_ids = list(_active_memberships(session, actor_id, space_context))
    if not actor_space_ids:
        return False
    stmt = select(SpaceProfileRef).where(
        SpaceProfileRef.user_id == target_id,
        SpaceProfileRef.status == "active",
        SpaceProfileRef.space_id.in_(actor_space_ids),
    )
    return session.scalar(stmt.limit(1)) is not None


def _pending_membership_link(
    session: Session, actor_id: int, target_id: int, space_context: int | None
) -> bool:
    """同一空间中一方 pending 一方 active → 双方最小互见（不传递）。"""
    stmt = select(SpaceMember.space_id, SpaceMember.user_id, SpaceMember.status).where(
        SpaceMember.user_id.in_((actor_id, target_id)),
        SpaceMember.status.in_(("pending", "active")),
    )
    if space_context is not None:
        stmt = stmt.where(SpaceMember.space_id == space_context)
    per_space: dict[int, set[tuple[int, str]]] = {}
    for space_id, user_id, status in session.execute(stmt):
        per_space.setdefault(space_id, set()).add((user_id, status))
    for entries in per_space.values():
        users = {uid for uid, _ in entries}
        statuses = {status for _, status in entries}
        if users == {actor_id, target_id} and statuses == {"pending", "active"}:
            return True
    return False


def _base_level(
    session: Session, actor: User, target: User, space_context: int | None
) -> tuple[str, str]:
    """原始层级判定（不含 purpose 收紧）。返回 (level, source)。"""
    if actor.id == target.id:
        return LEVEL_SELF_PRIVATE, "self"
    # 代管创建者保有查看权，映射 household_detail（编辑权仍由 custody 判定）。
    # provisional 档案遵循最小节点规则：即使是代管人也不得越过 household_detail
    # 读取性别/精确生卒/简介等字段（F3 收紧，design D-04）。
    if space_context is None and target.created_by == actor.id:
        if target.profile_status == "provisional":
            return LEVEL_LINEAGE_SUMMARY, "custodian_provisional"
        return LEVEL_HOUSEHOLD_DETAIL, "custodian"
    household, lineage = _shared_space_kinds(session, actor.id, target.id, space_context)
    if household:
        return LEVEL_HOUSEHOLD_DETAIL, "household"
    if lineage:
        return LEVEL_LINEAGE_SUMMARY, "lineage_member"
    if _ref_link(session, actor.id, target.id, space_context):
        return LEVEL_LINEAGE_SUMMARY, "ref"
    if direct_structural_edge(session, actor.id, target.id) is not None:
        # v2 取消「直系自动 full」：跨 household 直系 ≤ lineage_summary
        return LEVEL_LINEAGE_SUMMARY, "direct_edge"
    if _pending_pair_edge(session, actor.id, target.id) is not None or _pending_membership_link(
        session, actor.id, target.id, space_context
    ):
        return LEVEL_LINEAGE_SUMMARY, "pending"
    return LEVEL_NONE, "none"


def is_minor(target: User) -> bool:
    """由结构化出生日期推导未成年（无法解析时按成年处理）。"""
    birth = target.birth if isinstance(target.birth, dict) else {}
    date_str = birth.get("date") if birth.get("cal_type") in ("solar", "lunar") else None
    if not date_str or len(date_str) < 10:
        return False
    try:
        from datetime import date as date_cls

        born = date_cls.fromisoformat(str(date_str)[:10])
    except ValueError:
        return False
    from app.utils.timeutil import utcnow

    today = utcnow().date()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return 0 <= age < _MINOR_YEAR_THRESHOLD


def evaluate(
    session: Session,
    actor: User,
    target: User,
    *,
    space_context: int | None = None,
    purpose: str = PURPOSE_PROFILE,
) -> VisibilityDecision:
    """统一可见性判定入口。space_context 将判定限定在单一空间内。

    purpose 只能收紧：agent/rag/search/statistics 的层级与投影不得超过 profile。
    """
    if purpose not in ALL_PURPOSES:  # pragma: no cover - 编程错误
        raise ValueError(f"unknown purpose: {purpose}")

    # A platform_operator is a platform identity, not a family-data identity.
    # Keep this guard before self/custody/membership/relationship precedence so a
    # combination identity cannot enter the family visibility chain by accident.
    if _is_platform_operator(session, actor):
        return VisibilityDecision(
            LEVEL_NONE,
            {field: FIELD_CLEAR for field in PROFILE_FIELDS},
            purpose,
        )

    base_level, source = _base_level(session, actor, target, space_context)

    # purpose 上限收紧（self 不受限）
    cap = _PURPOSE_LEVEL_CAP[purpose]
    level = base_level
    if level != LEVEL_SELF_PRIVATE and _LEVEL_ORDER[level] > _LEVEL_ORDER[cap]:
        level = cap

    fields = {field: FIELD_CLEAR for field in PROFILE_FIELDS}
    if level == LEVEL_NONE:
        return VisibilityDecision(LEVEL_NONE, fields, purpose)

    if level == LEVEL_LINEAGE_SUMMARY and source != "pending":
        # 显式披露只扩展字段投影；pending 最小互见不享受披露扩展
        disclosed = disclosed_categories(session, target, space_context)
        cleared: set[str] = set()
        for category in disclosed:
            cleared.update(CATEGORY_FIELD_MAP.get(category, ()))
    elif level == LEVEL_LINEAGE_SUMMARY:
        cleared = set()
    else:
        cleared = set(CONTENT_FIELDS)

    # 未成年人 overlay：最后收紧，任何来源/披露不可越过（本人除外）
    if level != LEVEL_SELF_PRIVATE and is_minor(target):
        cleared.difference_update(MINOR_OVERLAY_FIELDS)

    for field in CONTENT_FIELDS:
        if field not in cleared:
            fields[field] = FIELD_MASKED
    return VisibilityDecision(level, fields, purpose)


def payload_from_decision(decision: VisibilityDecision, target: User) -> dict[str, Any]:
    """按 decision 构造对外载荷；masked 字段以 MASKED 哨兵替换（v1 兼容形状）。"""
    claim_status = target.account.status if getattr(target, "account", None) else "managed"
    values: dict[str, Any] = {
        "id": target.id,
        "name": target.name,
        "gender": target.gender,
        "birth": target.birth,
        "death": target.death,
        "bio": target.bio,
        "avatar_path": target.avatar_path,
        "privacy_mode": target.privacy_mode,
        "claim_status": claim_status,
        "created_by": target.created_by,
        "created_at": target.created_at,
    }
    out: dict[str, Any] = {}
    for field in PROFILE_FIELDS:
        if decision.fields.get(field) == FIELD_MASKED:
            out[field] = dict(MASKED)
        else:
            out[field] = values[field]
    return out


def user_payload_for(
    session: Session,
    viewer: User,
    target: User,
    *,
    purpose: str = PURPOSE_PROFILE,
) -> dict[str, Any] | None:
    """对外用户载荷；invisible → None（路由层转 404 防枚举）。"""
    decision = evaluate(session, viewer, target, purpose=purpose)
    if not decision.visible:
        return None
    return payload_from_decision(decision, target)


def visible_user_ids(session: Session, actor: User) -> set[int]:
    """actor 可见（level != none）的全部档案 id 集合（搜索/统计/图候选）。"""
    candidates = {actor.id}
    my_active_spaces = list(_active_memberships(session, actor.id, None))
    if my_active_spaces:
        space_ids = list(my_active_spaces)
        member_ids = set(
            session.scalars(
                select(SpaceMember.user_id).where(
                    SpaceMember.space_id.in_(space_ids), SpaceMember.status == "active"
                )
            ).all()
        )
        ref_ids = set(
            session.scalars(
                select(SpaceProfileRef.user_id).where(
                    SpaceProfileRef.space_id.in_(space_ids), SpaceProfileRef.status == "active"
                )
            ).all()
        )
        candidates |= member_ids | ref_ids
    edge_pairs = session.scalars(
        select(Relation).where(Relation.status.in_(("active", "pending")))
    ).all()
    for edge in edge_pairs:
        if actor.id == edge.from_user:
            candidates.add(edge.to_user)
        elif actor.id == edge.to_user:
            candidates.add(edge.from_user)
    return {
        uid
        for uid in candidates
        if uid == actor.id
        or (
            (target := session.get(User, uid)) is not None
            and evaluate(session, actor, target).visible
        )
    }


__all__ = [
    "ALL_PURPOSES",
    "BASELINE_FIELDS",
    "CATEGORY_FIELD_MAP",
    "CONTENT_FIELDS",
    "FIELD_CLEAR",
    "FIELD_MASKED",
    "HIGH_RISK_CATEGORIES",
    "LEVEL_HOUSEHOLD_DETAIL",
    "LEVEL_LINEAGE_SUMMARY",
    "LEVEL_NONE",
    "LEVEL_SELF_PRIVATE",
    "MASKED",
    "MINOR_OVERLAY_FIELDS",
    "PROFILE_FIELDS",
    "PURPOSE_AGENT",
    "PURPOSE_EXPORT",
    "PURPOSE_GRAPH",
    "PURPOSE_PROFILE",
    "PURPOSE_RAG",
    "PURPOSE_SEARCH",
    "PURPOSE_STATISTICS",
    "VisibilityDecision",
    "direct_structural_edge",
    "evaluate",
    "is_minor",
    "payload_from_decision",
    "user_payload_for",
    "visible_user_ids",
]
