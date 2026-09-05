"""披露偏好读写（v2 Foundation，spec/architecture.md §0.1）。

disclosure_preferences 是字段级披露的唯一权威存储（users.clan_disclosure_json
已删除）。scope='global' 为全局偏好，可被 scope='space' 的逐空间覆盖；
默认全部不公开。披露只扩展 lineage_summary 层的字段投影，不单独授予可见性。

09-05 高敏感策略放开（用户决策）：health/address/school/contact/private_notes
五类允许**本人**显式开启（全局与逐空间），与基础五类同一路径落行；未成年人
（visibility.is_minor）的高敏感开启请求整体 422（DISCLOSURE_MINOR_FORBIDDEN），
始终最小披露；默认仍为关闭。高敏感档案内容列当前尚未落库，放开的只是写入
合同——可见性行为不变（minor overlay 仍由 visibility 层强制）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import DISCLOSURE_MINOR_FORBIDDEN, raise_api_error
from app.models.user import (
    BASIC_DISCLOSURE_KEYS,
    DISCLOSURE_KEYS,
    HIGH_SENSITIVE_DISCLOSURE_KEYS,
    User,
)
from app.models.v2_foundation import DisclosurePreference
from app.utils.timeutil import utcnow

_MINOR_FORBIDDEN_MESSAGE = "未成年人档案的高敏感类别始终按最小披露，不可开启"


def _assert_high_risk_writable(target: User, flags: dict[str, bool]) -> None:
    """未成年人守卫：is_minor 档案的高敏感开启请求整体 422（防枚举文案不含年龄细节）。

    visibility 与本模块互相 import，按项目惯例在函数内延迟解析。
    """
    if not any(bool(flags.get(key)) for key in HIGH_SENSITIVE_DISCLOSURE_KEYS):
        return
    from app.services.visibility import is_minor

    if is_minor(target):
        raise_api_error(422, DISCLOSURE_MINOR_FORBIDDEN, _MINOR_FORBIDDEN_MESSAGE)


def disclosed_categories(
    session: Session,
    target: User,
    space_context: int | None = None,
) -> frozenset[str]:
    """目标当前对外公开的类别集合。

    逐空间行双向覆盖全局行（true 扩展、false 收紧）；space_context=None 时
    仅全局生效。高敏感类（health/address/school/contact/private_notes）自
    09-05 起与基础类同一合同：本人可显式落 true 行，本函数如实合并；当前
    高敏感档案内容列尚未落库，故无可见性变化。未成年人始终由 visibility
    overlay 按最小披露遮蔽。
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
    return frozenset(k for k, v in merged.items() if v)


def _upsert_space_row(
    session: Session,
    target_id: int,
    category: str,
    space_id: int,
    allowed: bool,
    now: datetime,
) -> bool:
    """逐空间行 upsert；返回该行值是否发生变化。"""
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
        return bool(allowed)  # 缺省 False → 落 true 即变化
    changed = bool(row.allowed) != allowed
    row.allowed = allowed
    row.updated_at = now
    return changed


def set_basic_disclosure(session: Session, target: User, flags: dict[str, bool]) -> list[str]:
    """整体替换全局偏好行（PUT /users/{id}/disclosure 兼容语义）。

    默认即不公开：仅落 true 行，false 语义由缺省表达（已有行则更新值）。
    09-05 起高敏感类别同路径落行（is_minor 开启请求已被守卫整体 422）。
    返回值发生变动的高敏感类别列表（供命令层写审计，不含任何内容文本）。
    """
    _assert_high_risk_writable(target, flags)
    now = utcnow()
    changed_high_risk: list[str] = []
    for category in DISCLOSURE_KEYS:
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
                if category in HIGH_SENSITIVE_DISCLOSURE_KEYS:
                    changed_high_risk.append(category)
        else:
            if category in HIGH_SENSITIVE_DISCLOSURE_KEYS and bool(row.allowed) != allowed:
                changed_high_risk.append(category)
            row.allowed = allowed
            row.updated_at = now
    session.flush()
    return changed_high_risk


def set_space_disclosure(
    session: Session,
    target: User,
    space_id: int,
    flags: dict[str, bool],
) -> list[str]:
    """整体替换逐空间覆盖行（仅档案本人可调，命令层强制）。

    与全局行不同：逐空间行显式落 false 行 —— 语义是「收紧」全局 true
    （disclosed_categories 双向合并）；09-05 起高敏感类别同路径落行
    （is_minor 开启请求已被守卫整体 422）。返回值发生变动的高敏感类别列表。
    """
    _assert_high_risk_writable(target, flags)
    now = utcnow()
    changed_high_risk: list[str] = []
    for category in DISCLOSURE_KEYS:
        if category not in flags:
            continue
        changed = _upsert_space_row(
            session, target.id, category, space_id, bool(flags[category]), now
        )
        if changed and category in HIGH_SENSITIVE_DISCLOSURE_KEYS:
            changed_high_risk.append(category)
    session.flush()
    return changed_high_risk


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
