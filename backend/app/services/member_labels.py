"""成员间自由关系词标注（09-20）。

「我和对方是什么关系」是**标注**，不是亲属事实：本模块只读写
``member_relation_labels``，绝不触碰 source_facts / relations / social_relations，
也绝不参与关系路径推导、可达性、世代计算或拓扑边。

授权口径：
- 写：只有该对两端本人可改（调用方先做端点判定）；
- 读：按调用方给出的授权节点集合过滤，两端都在集合内才返回。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import VALIDATION_ERROR, raise_api_error
from app.models.space import MemberRelationLabel
from app.utils.timeutil import utcnow

LABEL_MAX_LENGTH = 64


def normalize_pair(user_a_id: int, user_b_id: int) -> tuple[int, int]:
    """端点规范化：较小 id 恒为 user_a_id，保证一对人一条标注。"""
    return (user_a_id, user_b_id) if user_a_id < user_b_id else (user_b_id, user_a_id)


def clean_label(raw: str | None) -> str | None:
    """清洗自由文本：去空白后为空 → None（表示移除标注）；超长 → 422。"""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > LABEL_MAX_LENGTH:
        raise_api_error(
            422,
            VALIDATION_ERROR,
            f"关系词最多 {LABEL_MAX_LENGTH} 个字符",
            detail={"max_length": LABEL_MAX_LENGTH},
        )
    return text


def require_label(raw: str | None) -> str:
    """加入空间时必须提供非空关系词（09-20）：缺失/空白 → 422。"""
    text = clean_label(raw)
    if text is None:
        raise_api_error(422, VALIDATION_ERROR, "请填写与对方的关系")
    return text


def upsert_label(
    session: Session,
    *,
    space_id: int,
    user_a_id: int,
    user_b_id: int,
    label: str | None,
    actor_user_id: int,
) -> MemberRelationLabel | None:
    """设置或清除一对成员的关系词标注（调用方已校验端点资格）。"""
    text = clean_label(label)
    first, second = normalize_pair(user_a_id, user_b_id)
    row = session.scalar(
        select(MemberRelationLabel).where(
            MemberRelationLabel.space_id == space_id,
            MemberRelationLabel.user_a_id == first,
            MemberRelationLabel.user_b_id == second,
        )
    )
    if text is None:
        if row is not None:
            session.delete(row)
            session.flush()
        return None
    now = utcnow()
    if row is None:
        row = MemberRelationLabel(
            space_id=space_id,
            user_a_id=first,
            user_b_id=second,
            label=text,
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.label = text
        row.updated_at = now
    session.flush()
    return row


def labels_for(
    session: Session, *, space_id: int, visible_ids: set[int] | None = None
) -> list[dict[str, Any]]:
    """该空间内的关系词标注；``visible_ids`` 给定时两端都必须在内。

    只读。任一端退出/被踢/不再可见时，调用方传入的集合自然排除该边。
    """
    rows = session.scalars(
        select(MemberRelationLabel)
        .where(MemberRelationLabel.space_id == space_id)
        .order_by(MemberRelationLabel.id)
    ).all()
    out: list[dict[str, Any]] = []
    for row in rows:
        if visible_ids is not None and (
            row.user_a_id not in visible_ids or row.user_b_id not in visible_ids
        ):
            continue
        out.append(
            {
                "id": f"label-{row.id}",
                "from_user_id": row.user_a_id,
                "to_user_id": row.user_b_id,
                "label": row.label,
            }
        )
    return out


def pair_for(
    session: Session, *, space_id: int, user_a_id: int, user_b_id: int
) -> MemberRelationLabel | None:
    """取一对成员的标注行（无则 None）。"""
    first, second = normalize_pair(user_a_id, user_b_id)
    return session.scalar(
        select(MemberRelationLabel).where(
            MemberRelationLabel.space_id == space_id,
            MemberRelationLabel.user_a_id == first,
            MemberRelationLabel.user_b_id == second,
        )
    )


__all__ = [
    "LABEL_MAX_LENGTH",
    "clean_label",
    "labels_for",
    "normalize_pair",
    "pair_for",
    "require_label",
    "upsert_label",
]
