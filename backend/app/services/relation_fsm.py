"""Relation FSM 与世代一致性校验（architecture.md §4 [AD-4]）。

转换表（终态不可复活，重连 = 新边）：
    pending --accept--> active        pending --reject--> rejected
    pending --cancel--> cancelled     active  --revoke--> revoked

权限：
    accept 仅被请求方(to_user)；cancel 仅发起方(from_user)；
    reject 仅被请求方；revoke 任一方。

世代校验：层级边（elder/younger）写入时做带偏移 BFS——
    elder 边 f→t：gen(t)=gen(f)-1；younger 边 f→t：gen(t)=gen(f)+1。
    覆盖两类冲突：①纯有向环（长辈链回到自身）②同代矛盾
    （如甲乙互为对方长辈的传递推论）。spouse/peer 边不参与。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.errors import (
    RELATION_CYCLE_FORBIDDEN,
    RELATION_INVALID_TRANSITION,
    raise_api_error,
)
from app.models.relation import Relation

# (当前状态, 动作) -> 新状态
TRANSITIONS: dict[tuple[str, str], str] = {
    ("pending", "accept"): "active",
    ("pending", "reject"): "rejected",
    ("pending", "cancel"): "cancelled",
    ("active", "revoke"): "revoked",
}

# 各动作的允许操作者（相对边的两端）
ACTOR_RULES: dict[str, tuple[str, ...]] = {
    # accept: 被请求方；cancel: 发起方；reject: 被请求方；revoke: 任一方
    "accept": ("to",),
    "cancel": ("from",),
    "reject": ("to",),
    "revoke": ("from", "to"),
}


def _now() -> datetime:
    from app.utils.timeutil import utcnow

    return utcnow()


def assert_actor_allowed(edge: Relation, actor_id: int, action: str) -> None:
    """校验操作者是边的合法一端且动作权限匹配。"""
    roles = set()
    if actor_id == edge.from_user:
        roles.add("from")
    if actor_id == edge.to_user:
        roles.add("to")
    allowed = ACTOR_RULES[action]
    if not roles & set(allowed):
        raise_api_error(
            403,
            "RELATION_FORBIDDEN_ACTOR",
            "当前用户无权执行该操作",
            detail={"action": action, "required_roles": list(allowed)},
        )


def transition(edge: Relation, action: str, actor_id: int, session: Session) -> Relation:
    """FSM 校验 + 状态迁移（调用方负责 commit）。"""
    assert_actor_allowed(edge, actor_id, action)
    new_status = TRANSITIONS.get((edge.status, action))
    if new_status is None:
        raise_api_error(
            409,
            RELATION_INVALID_TRANSITION,
            "关系当前状态不允许该操作",
            detail={"current_status": edge.status, "action": action},
        )
    edge.status = new_status
    edge.updated_at = _now()
    session.flush()
    return edge


def _active_elder_edges_from(session: Session, user_id: int) -> list[Relation]:
    """user 作为晚辈方向的活动 elder 边（user 是这些边的 to_user）。"""
    stmt = select(Relation).where(
        Relation.to_user == user_id,
        Relation.dir_class == "elder",
        Relation.status == "active",
    )
    return list(session.scalars(stmt))


def _active_hierarchy_edges(session: Session) -> list[Relation]:
    """全部活动层级边（elder/younger）；spouse/peer 不约束世代。"""
    stmt = select(Relation).where(
        Relation.dir_class.in_(("elder", "younger")),
        Relation.status == "active",
    )
    return list(session.scalars(stmt))


def assert_generation_consistent(
    session: Session, from_user: int, to_user: int, dir_class: str
) -> None:
    """写入层级边前的世代一致性校验（覆盖纯有向环检测漏掉的矛盾场景）。

    世代偏移约定：gen 沿 elder 边 f→t 为 gen(t)=gen(f)-1；沿 younger 边为 +1。
    从 from_user 出发对现有活动层级边做带偏移 BFS；若 to_user 已被赋值且与
    新边要求的相对偏移冲突 → 422 RELATION_CYCLE_FORBIDDEN。
    """
    required = -1 if dir_class == "elder" else 1  # gen(to) - gen(from)
    if dir_class == "spouse":
        return
    offsets: dict[int, int] = {from_user: 0}
    stack = [from_user]
    adj: dict[int, list[tuple[int, int]]] = {}
    for edge in _active_hierarchy_edges(session):
        delta = -1 if edge.dir_class == "elder" else 1  # gen(to)-gen(from)
        adj.setdefault(edge.from_user, []).append((edge.to_user, delta))
        adj.setdefault(edge.to_user, []).append((edge.from_user, -delta))
    while stack:
        cur = stack.pop()
        for nb, d in adj.get(cur, []):
            nxt = offsets[cur] + d
            if nb in offsets:
                if offsets[nb] != nxt:
                    raise_api_error(
                        422,
                        RELATION_CYCLE_FORBIDDEN,
                        "该关系会形成世代矛盾，无法建立",
                        detail={"from_user": from_user, "to_user": to_user},
                    )
            else:
                offsets[nb] = nxt
                stack.append(nb)
    if to_user in offsets and offsets[to_user] != required:
        raise_api_error(
            422,
            RELATION_CYCLE_FORBIDDEN,
            "该关系会形成世代矛盾，无法建立",
            detail={
                "from_user": from_user,
                "to_user": to_user,
                "existing_offset": offsets[to_user],
                "required_offset": required,
            },
        )


def pair_has_non_terminal_edge(session: Session, user_a: int, user_b: int) -> Relation | None:
    """同对用户是否已存在非终态边（双向）。"""
    stmt = select(Relation).where(
        or_(
            (Relation.from_user == user_a) & (Relation.to_user == user_b),
            (Relation.from_user == user_b) & (Relation.to_user == user_a),
        ),
        Relation.status.in_(("pending", "active")),
    )
    return session.scalar(stmt)


def create_relation(
    session: Session,
    *,
    from_user: int,
    to_user: int,
    dir_class: str,
    label: str | None,
    status: str = "pending",
) -> Relation:
    """创建关系边：自环/重复对/世代一致性三道校验后插入。"""
    if from_user == to_user:
        raise_api_error(422, "RELATION_SELF_FORBIDDEN", "不能与自己建立关系")
    existing = pair_has_non_terminal_edge(session, from_user, to_user)
    if existing is not None:
        raise_api_error(
            409,
            "RELATION_DUPLICATE_PAIR",
            "你们之间已存在一条待处理或已生效的关系",
            detail={"relation_id": existing.id, "status": existing.status},
        )
    if dir_class in ("elder", "younger"):
        assert_generation_consistent(session, from_user, to_user, dir_class)
    now = _now()
    edge = Relation(
        from_user=from_user,
        to_user=to_user,
        dir_class=dir_class,
        label=label,
        created_by=from_user,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(edge)
    try:
        session.flush()
    except Exception:  # pragma: no cover - CHECK/index 兜底
        session.rollback()
        raise_api_error(409, "RELATION_DUPLICATE_PAIR", "关系写入冲突")
    return edge
