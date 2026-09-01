"""人物身份匹配策略：同一空间内的重复建档判定口径（Steward 拥有）。

一个 ``User`` 行携带一个 ``Account`` 与一次性 PIN，所以"同一个人有两份档案"
等于真的存在两份可登录凭据——这是身份问题，不只是数据质量问题。

本模块是该判定的**唯一真源**：建档写入门禁（``commands/members.py``）与
Steward 回溯审计都调用这里，避免阈值在两处漂移。归一与强度判定是纯函数；
``find_duplicate_candidates`` 是唯一读库的入口，两个调用方共用它。

## 匹配键只用于比对，绝不回写

``users.name`` 是待本人确认的 provisional 数据（``ProfileFactReview`` 的
name 必审项）；本人认领后有权把名字改回自己要的写法。因此归一只作用于
派生的比对键，永不覆写存储值。

## 为什么不落列、不建唯一索引

身份键派生自 ``users``，而空间作用域在 ``space_profile_refs``/``space_members``
上（一个 user 可属多个空间）。索引建在 ``users`` 上只能保证全局唯一——那是错的，
两个不相干家庭各有一个"李秀英 1948-03-12"必须都允许。建在 ref 表上则键被反
规范化，改名（三处入口）与生日编辑任一漏同步就静默失效。

因此并发保证改由 ``BEGIN IMMEDIATE`` 提供（SQLite 单写者，写锁前置后
检查与插入之间没有竞态窗口），键在查询时现算。家族空间是几十到几百人量级，
全扫开销可忽略。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from zhconv import convert as _zh_convert

from app.models.space import SpaceMember, SpaceProfileRef
from app.models.user import User
from app.services.lunar import lunar_to_solar

MatchStrength = Literal["strong", "weak", "none"]

STRENGTH_STRONG: Final[MatchStrength] = "strong"
STRENGTH_WEAK: Final[MatchStrength] = "weak"
STRENGTH_NONE: Final[MatchStrength] = "none"

_WHITESPACE_RE: Final = re.compile(r"\s+")
# 姓名里的分隔与装饰符号不参与身份判定（"李 秀英"、"李·秀英"、"李-秀英" 同一人）
_PUNCT_RE: Final = re.compile(r"[·・．.,，、_\-—–~～'\"“”‘’()（）\[\]【】]")


def normalize_person_name(name: str | None) -> str:
    """姓名 → 比对键：NFKC → 去空白与分隔符 → 繁转简 → casefold。

    繁简归一到简体（产品裁定）。zhconv 顺带处理部分异体字（峯→峰、淩→凌），
    但不覆盖全部（喆 保持原样）——未覆盖的异体字会落到 weak 匹配要求消歧，
    而不是被静默并成同一人。
    """
    if not name:
        return ""
    folded = unicodedata.normalize("NFKC", name)
    folded = _WHITESPACE_RE.sub("", folded)
    folded = _PUNCT_RE.sub("", folded)
    folded = _zh_convert(folded, "zh-cn")
    return folded.casefold()


def canonical_birth(birth: Any) -> str | None:
    """生日 → 可比对的公历 ISO 键；无法定位到某一天时返回 None。

    农历自行换算而不读 ``mirror_date``：``StructuredDate.date`` 恒为 ISO，而
    ``lunar.lunar_to_solar`` 期望 ``'YYYY:M:D'``，故 ``enrich_structured_date``
    对 lunar 输入产出的 ``mirror_date`` 恒为 None（既有缺陷）。这里做正确换算，
    使农历与公历录入的同一天能够匹配。

    闰月无法表达：ISO 容器存不下 ``lunar_to_solar`` 用来标闰月的负数月份，
    故闰月生日会被当作平月换算。这是数据模型的既有限制。
    """
    if not isinstance(birth, dict):
        return None
    cal_type = birth.get("cal_type")
    raw = birth.get("date")
    if cal_type not in ("solar", "lunar") or not isinstance(raw, str) or not raw:
        return None
    if cal_type == "solar":
        return raw
    parts = raw.split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
    except ValueError:
        return None
    return lunar_to_solar(f"{year}:{month}:{day}")


def match_strength(
    *,
    name_key: str,
    birth_key: str | None,
    other_name_key: str,
    other_birth_key: str | None,
) -> MatchStrength:
    """两份档案的重复强度。

    - ``strong``：姓名键相同且两侧都有生日且生日相同 → 判定为同一人，拒绝建档。
    - ``weak``：姓名键相同但任一侧生日缺失 → 不可判定，要求创建者显式消歧。
    - ``none``：姓名键不同，或双方生日都有但不同（同名不同人，正常放行）。

    生日在两侧都存在且不同时是 ``none`` 而非 ``weak``：那是同名的不同人，
    大家族里跨辈同名常见，不应反复追问。
    """
    if not name_key or name_key != other_name_key:
        return STRENGTH_NONE
    if birth_key is not None and other_birth_key is not None:
        return STRENGTH_STRONG if birth_key == other_birth_key else STRENGTH_NONE
    return STRENGTH_WEAK


@dataclass(frozen=True)
class DuplicateCandidate:
    """空间内一个疑似同一人的既有档案。"""

    user_id: int
    name: str
    strength: MatchStrength
    birth_key: str | None


@dataclass(frozen=True)
class DuplicatePair:
    """空间内一对疑似同一人的档案（``user_ids`` 升序，便于稳定签名）。"""

    user_ids: tuple[int, int]
    strength: MatchStrength


def find_duplicate_pairs(users: Iterable[Any]) -> list[DuplicatePair]:
    """给定人物集合中所有疑似同一人的配对，strong 优先、id 升序。

    供 Steward 回溯审计使用：写入门禁只能看见"当下"，而身份重复是**涌现属性**
    ——先建的档案没填生日、本人认领后补上，此刻才与另一份撞上。那一刻没有任何
    写入路径在跑，只有持续审计能发现。

    先按姓名键分组再组内两两比：同名是重复的必要条件，分组把 O(n²) 压到
    只在同名簇内展开。
    """
    grouped: dict[str, list[tuple[int, str | None]]] = {}
    for user in users:
        name_key = normalize_person_name(getattr(user, "name", None))
        if not name_key:
            continue
        grouped.setdefault(name_key, []).append(
            (int(user.id), canonical_birth(getattr(user, "birth", None)))
        )
    pairs: list[DuplicatePair] = []
    for name_key, members in grouped.items():
        if len(members) < 2:
            continue
        members.sort()
        for index, (left_id, left_birth) in enumerate(members):
            for right_id, right_birth in members[index + 1 :]:
                strength = match_strength(
                    name_key=name_key,
                    birth_key=left_birth,
                    other_name_key=name_key,
                    other_birth_key=right_birth,
                )
                if strength != STRENGTH_NONE:
                    pairs.append(DuplicatePair(user_ids=(left_id, right_id), strength=strength))
    pairs.sort(key=lambda p: (p.strength != STRENGTH_STRONG, p.user_ids))
    return pairs


def find_duplicate_candidates(
    session: Session,
    *,
    space_id: int,
    name: str,
    birth: Any,
    exclude_user_id: int | None = None,
) -> list[DuplicateCandidate]:
    """目标空间内与 (name, birth) 疑似同一人的既有档案，strong 优先。

    "在该空间"同时含 ``SpaceProfileRef``（provisional 引用）与 ``SpaceMember``
    （已认领成员）——两者都占据该空间的人物身份，都要参与去重。

    键在查询时现算而不落列：见模块文档「为什么不落列」。空间人数是几十到
    几百量级，全扫开销可忽略，换来的是改名/生日编辑无需同步任何派生列。
    """
    name_key = normalize_person_name(name)
    if not name_key:
        return []
    birth_key = canonical_birth(birth)
    stmt = (
        select(User)
        .where(User.deleted_at.is_(None))
        .where(
            or_(
                User.id.in_(
                    select(SpaceProfileRef.user_id).where(
                        SpaceProfileRef.space_id == space_id,
                        SpaceProfileRef.status == "active",
                    )
                ),
                User.id.in_(select(SpaceMember.user_id).where(SpaceMember.space_id == space_id)),
            )
        )
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    found: list[DuplicateCandidate] = []
    for row in session.scalars(stmt):
        other_birth_key = canonical_birth(row.birth)
        strength = match_strength(
            name_key=name_key,
            birth_key=birth_key,
            other_name_key=normalize_person_name(row.name),
            other_birth_key=other_birth_key,
        )
        if strength != STRENGTH_NONE:
            found.append(
                DuplicateCandidate(
                    user_id=row.id,
                    name=row.name,
                    strength=strength,
                    birth_key=other_birth_key,
                )
            )
    found.sort(key=lambda c: (c.strength != STRENGTH_STRONG, c.user_id))
    return found
