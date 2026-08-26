"""披露偏好读写（v2 Foundation，spec/architecture.md §0.1）。

disclosure_preferences 是字段级披露的唯一权威存储（users.clan_disclosure_json
已删除）。scope='global' 为全局偏好，可被 scope='space' 的逐空间覆盖；
默认全部不公开。披露只扩展 lineage_summary 层的字段投影，不单独授予可见性。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import (
    BASIC_DISCLOSURE_KEYS,
    DISCLOSURE_KEYS,
    HIGH_SENSITIVE_DISCLOSURE_KEYS,
    User,
)
from app.models.v2_foundation import DisclosurePreference
from app.utils.timeutil import utcnow


def disclosed_categories(
    session: Session,
    target: User,
    space_context: int | None = None,
) -> frozenset[str]:
    """目标当前对外公开的类别集合。

    逐空间行双向覆盖全局行（true 扩展、false 收紧）；space_context=None 时
    仅全局生效。高敏感类（health/address/school/contact/private_notes）即使
    allowed=True，也只对明确授权的投影生效，任何层级不得自动开放（visibility
    overlay 消费）——写入侧已恒拒绝 true，此处仅作防御性过滤。
    """
    rows = session.scalars(
        select(DisclosurePreference).where(DisclosurePreference.profile_id == target.id)
    ).all()
    global_flags: dict[str, bool] = {}
    overrides: dict[int, dict[str, bool]] = {}
    for row in rows:
        if row.scope == "global":
            global_flags[row.category] = bool(row.allowed)
        elif row.space_id is not None:
            overrides.setdefault(row.space_id, {})[row.category] = bool(row.allowed)
    merged = global_flags
    if space_context is not None and space_context in overrides:
        merged = {**global_flags, **overrides[space_context]}
    return frozenset(k for k, v in merged.items() if v and k not in HIGH_SENSITIVE_DISCLOSURE_KEYS)


def _upsert_space_row(
    session: Session,
    target_id: int,
    category: str,
    space_id: int,
    allowed: bool,
    now: datetime,
) -> None:
    row = session.scalar(
        select(DisclosurePreference).where(
            DisclosurePreference.profile_id == target_id,
            DisclosurePreference.category == category,
            DisclosurePreference.scope == "space",
            DisclosurePreference.space_id == space_id,
        )
    )
    if row is None:
        session.add(
            DisclosurePreference(
                profile_id=target_id,
                category=category,
                scope="space",
                space_id=space_id,
                allowed=allowed,
                updated_at=now,
            )
        )
    else:
        row.allowed = allowed
        row.updated_at = now


def set_basic_disclosure(session: Session, target: User, flags: dict[str, bool]) -> None:
    """整体替换基础五类全局偏好（PUT /users/{id}/disclosure 兼容语义）。

    默认即不公开：仅落 true 行，false 语义由缺省表达（已有行则更新值）。
    """
    now = utcnow()
    for category in BASIC_DISCLOSURE_KEYS:
        if category not in flags:
            continue
        allowed = bool(flags[category])
        row = session.scalar(
            select(DisclosurePreference).where(
                DisclosurePreference.profile_id == target.id,
                DisclosurePreference.category == category,
                DisclosurePreference.scope == "global",
            )
        )
        if row is None:
            if allowed:
                session.add(
                    DisclosurePreference(
                        profile_id=target.id,
                        category=category,
                        scope="global",
                        space_id=None,
                        allowed=True,
                        updated_at=now,
                    )
                )
        else:
            row.allowed = allowed
            row.updated_at = now
    session.flush()


def set_space_disclosure(
    session: Session,
    target: User,
    space_id: int,
    flags: dict[str, bool],
) -> None:
    """整体替换基础五类逐空间覆盖行（仅档案本人可调，命令层强制）。

    与全局行不同：逐空间行显式落 false 行 —— 语义是「收紧」全局 true
    （disclosed_categories 双向合并）；高敏感类别不落行（恒不可开放）。
    """
    now = utcnow()
    for category in BASIC_DISCLOSURE_KEYS:
        if category not in flags:
            continue
        _upsert_space_row(session, target.id, category, space_id, bool(flags[category]), now)
    session.flush()


def disclosure_matrix(session: Session, target: User) -> dict[str, object]:
    """合并矩阵视图：全局偏好 + 逐空间覆盖（缺省类别一律 False）。"""
    global_flags = all_disclosure_flags(session, target)
    spaces: dict[int, dict[str, bool]] = {}
    rows = session.scalars(
        select(DisclosurePreference).where(
            DisclosurePreference.profile_id == target.id,
            DisclosurePreference.scope == "space",
        )
    ).all()
    for row in rows:
        if row.space_id is None:  # CHECK 约束已排除；防御类型收窄
            continue
        view = spaces.setdefault(row.space_id, {k: False for k in DISCLOSURE_KEYS})
        view[row.category] = bool(row.allowed)
    return {
        "global": global_flags,
        "spaces": [
            {"space_id": space_id, "allowed": flags} for space_id, flags in sorted(spaces.items())
        ],
    }


def basic_disclosure_flags(session: Session, target: User) -> dict[str, bool]:
    """基础五类布尔视图（对外载荷形状与 v1 clan_disclosure 一致，缺省 False）。"""
    rows = session.scalars(
        select(DisclosurePreference).where(
            DisclosurePreference.profile_id == target.id,
            DisclosurePreference.scope == "global",
        )
    ).all()
    stored = {row.category: bool(row.allowed) for row in rows}
    return {key: stored.get(key, False) for key in BASIC_DISCLOSURE_KEYS}


def all_disclosure_flags(session: Session, target: User) -> dict[str, bool]:
    """全类别布尔视图（含高敏感类；缺省 False）。"""
    rows = session.scalars(
        select(DisclosurePreference).where(
            DisclosurePreference.profile_id == target.id,
            DisclosurePreference.scope == "global",
        )
    ).all()
    stored = {row.category: bool(row.allowed) for row in rows}
    return {key: stored.get(key, False) for key in DISCLOSURE_KEYS}


__all__ = [
    "all_disclosure_flags",
    "basic_disclosure_flags",
    "disclosed_categories",
    "disclosure_matrix",
    "set_basic_disclosure",
    "set_space_disclosure",
]
