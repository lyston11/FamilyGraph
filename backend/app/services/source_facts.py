"""SourceFact 事实层服务（V2.3 Block E1，KI-1/KI-3）。

FSM（单向，终态不可复活，复用 profile_fact_reviews 风格）：
    proposed --confirm--> confirmed     proposed --dispute--> disputed
    confirmed --revoke-> revoked        disputed --confirm-> confirmed
                                        disputed --revoke--> revoked

每次 state/内容变更 revision+1 并写 domain_events（source_fact.confirmed /
.disputed / .revoked / .revised），payload 含 fact id/type/双方/space/revision，
供 E2 DerivedFact 缓存失效消费。由调用方事务统一提交。

方向合同：*_parent 类 subject 是 object 的父/母/监护人；成环检测对
confirmed parent 边双向各上溯 ≤32 层（PARENT_CHAIN_DEPTH_LIMIT）。
direct_sibling 允许父母未知独立存在；spouse/partner 允许多条历史与多条
并存（再婚），仅受同元组 partial unique 约束。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import (
    SOURCE_FACT_CYCLE_FORBIDDEN,
    SOURCE_FACT_DUPLICATE,
    SOURCE_FACT_INVALID_TRANSITION,
    SOURCE_FACT_SELF_FORBIDDEN,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.relationship_facts import (
    PARENT_FACT_TYPES,
    SOURCE_FACT_PROVENANCES,
    SOURCE_FACT_TYPES,
    RawRelationInput,
    SourceFact,
)
from app.models.v2_foundation import DomainEvent
from app.services.domain_events import emit as emit_domain_event
from app.utils.timeutil import utcnow

# ---- 状态与动作常量 ----
FACT_PROPOSED = "proposed"
FACT_CONFIRMED = "confirmed"
FACT_DISPUTED = "disputed"
FACT_REVOKED = "revoked"

ACTION_CONFIRM = "confirm"
ACTION_DISPUTE = "dispute"
ACTION_REVOKE = "revoke"

# (当前状态, 动作) -> 新状态
TRANSITIONS: dict[tuple[str, str], str] = {
    (FACT_PROPOSED, ACTION_CONFIRM): FACT_CONFIRMED,
    (FACT_PROPOSED, ACTION_DISPUTE): FACT_DISPUTED,
    (FACT_CONFIRMED, ACTION_REVOKE): FACT_REVOKED,
    (FACT_DISPUTED, ACTION_CONFIRM): FACT_CONFIRMED,
    (FACT_DISPUTED, ACTION_REVOKE): FACT_REVOKED,
}

# 新状态 -> 领域事件类型（E2 缓存失效按 type 消费）
EVENT_BY_STATE: dict[str, str] = {
    FACT_CONFIRMED: "source_fact.confirmed",
    FACT_DISPUTED: "source_fact.disputed",
    FACT_REVOKED: "source_fact.revoked",
}
EVENT_REVISED = "source_fact.revised"

AGGREGATE_TYPE = "source_fact"

# parent 类成环检测上溯深度上限（合同值；超深链不再上溯）
PARENT_CHAIN_DEPTH_LIMIT = 32


def create_raw_relation_input(
    session: Session,
    *,
    author_account_id: int,
    text: str,
    context: dict[str, Any],
) -> RawRelationInput:
    """保存自由输入原文（≤200 字，append-only；KI-3 原文不可覆盖）。"""
    stripped = text.strip()
    if not stripped:
        raise_api_error(422, VALIDATION_ERROR, "关系描述不能为空")
    if len(stripped) > 200:
        raise_api_error(422, VALIDATION_ERROR, "关系描述不能超过 200 字")
    row = RawRelationInput(
        author_account_id=author_account_id,
        text=stripped,
        context_json=context,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _fact_payload(fact: SourceFact) -> dict[str, Any]:
    return {
        "fact_id": fact.id,
        "fact_type": fact.fact_type,
        "subject_user_id": fact.subject_user_id,
        "object_user_id": fact.object_user_id,
        "space_id": fact.space_id,
        "revision": fact.revision,
    }


def _emit_fact_event(
    session: Session,
    *,
    event_type: str,
    fact: SourceFact,
    actor_account_id: int | None,
) -> DomainEvent:
    return emit_domain_event(
        session,
        event_type=event_type,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=fact.id,
        payload=_fact_payload(fact),
        space_id=fact.space_id,
        actor_account_id=actor_account_id,
    )


def _ancestors_within(session: Session, start_user_id: int, limit: int) -> set[int]:
    """自 start 沿 confirmed parent 边（child→parent 方向）上溯 ≤limit 层的祖先集合。"""
    visited = {start_user_id}
    frontier = [start_user_id]
    for _depth in range(limit):
        if not frontier:
            return visited
        stmt = select(SourceFact.subject_user_id).where(
            SourceFact.fact_type.in_(PARENT_FACT_TYPES),
            SourceFact.state == FACT_CONFIRMED,
            SourceFact.object_user_id.in_(frontier),
        )
        next_frontier: list[int] = []
        for parent in session.scalars(stmt):
            pid = int(parent)
            if pid not in visited:
                visited.add(pid)
                next_frontier.append(pid)
        frontier = next_frontier
    return visited


def _assert_no_parent_cycle(session: Session, subject_user_id: int, object_user_id: int) -> None:
    """parent 类写入前成环检测：新边任一端经对方上溯可达即成环。

    边语义 subject=parent/object=child。两个方向都要查：
    - 从 object 上溯命中 subject：object 已是 subject 的后裔；
    - 从 subject 上溯命中 object：subject 已是 object 的后裔（如互为家长）。
    深度上限 PARENT_CHAIN_DEPTH_LIMIT，超出不再上溯（合同上限）。
    """
    detail: dict[str, object] = {
        "subject_user_id": subject_user_id,
        "object_user_id": object_user_id,
    }
    if subject_user_id in _ancestors_within(session, object_user_id, PARENT_CHAIN_DEPTH_LIMIT):
        raise_api_error(
            422, SOURCE_FACT_CYCLE_FORBIDDEN, "该亲子事实会形成环路，无法建立", detail=detail
        )
    if object_user_id in _ancestors_within(session, subject_user_id, PARENT_CHAIN_DEPTH_LIMIT):
        raise_api_error(
            422, SOURCE_FACT_CYCLE_FORBIDDEN, "该亲子事实会形成环路，无法建立", detail=detail
        )


def _find_active_duplicate(
    session: Session,
    *,
    fact_type: str,
    subject_user_id: int,
    object_user_id: int,
    space_id: int | None,
) -> SourceFact | None:
    stmt = select(SourceFact).where(
        SourceFact.fact_type == fact_type,
        SourceFact.subject_user_id == subject_user_id,
        SourceFact.object_user_id == object_user_id,
        SourceFact.state != FACT_REVOKED,
    )
    if space_id is None:
        stmt = stmt.where(SourceFact.space_id.is_(None))
    else:
        stmt = stmt.where(SourceFact.space_id == space_id)
    return session.scalar(stmt)


def create_source_fact(
    session: Session,
    *,
    fact_type: str,
    subject_user_id: int,
    object_user_id: int,
    provenance: str,
    space_id: int | None = None,
    asserted_by_account_id: int | None = None,
    raw_text_id: int | None = None,
    state: str = FACT_PROPOSED,
) -> SourceFact:
    """创建原子事实；parent 类先做环检测，同元组非 revoked 唯一。

    初始 state 仅允许 proposed（默认）或 confirmed（导入/种子映射等已证路径）；
    直接落 confirmed 时同步写 source_fact.confirmed 事件（revision=1）。
    """
    if fact_type not in SOURCE_FACT_TYPES:
        raise_api_error(422, VALIDATION_ERROR, f"未知事实类型 {fact_type}")
    if provenance not in SOURCE_FACT_PROVENANCES:
        raise_api_error(422, VALIDATION_ERROR, f"未知来源 {provenance}")
    if state not in (FACT_PROPOSED, FACT_CONFIRMED):
        raise_api_error(422, VALIDATION_ERROR, "新建事实只能处于 proposed 或 confirmed 态")
    if subject_user_id == object_user_id:
        raise_api_error(422, SOURCE_FACT_SELF_FORBIDDEN, "不能与自己建立亲属事实")
    duplicate = _find_active_duplicate(
        session,
        fact_type=fact_type,
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
        space_id=space_id,
    )
    if duplicate is not None:
        raise_api_error(
            409,
            SOURCE_FACT_DUPLICATE,
            "两人之间已存在同类型的有效事实",
            detail={"source_fact_id": duplicate.id, "state": duplicate.state},
        )
    if fact_type in PARENT_FACT_TYPES:
        _assert_no_parent_cycle(session, subject_user_id, object_user_id)
    now = utcnow()
    fact = SourceFact(
        fact_type=fact_type,
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
        space_id=space_id,
        asserted_by_account_id=asserted_by_account_id,
        provenance=provenance,
        state=state,
        raw_text_id=raw_text_id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(fact)
    session.flush()
    if state == FACT_CONFIRMED:
        _emit_fact_event(
            session, event_type=EVENT_BY_STATE[FACT_CONFIRMED], fact=fact, actor_account_id=None
        )
    return fact


def transition_source_fact(
    session: Session,
    fact: SourceFact,
    action: str,
    *,
    actor_account_id: int | None = None,
) -> SourceFact:
    """FSM 校验 + 状态迁移；revision+1 并写对应领域事件（调用方负责 commit）。"""
    new_state = TRANSITIONS.get((fact.state, action))
    if new_state is None:
        raise_api_error(
            409,
            SOURCE_FACT_INVALID_TRANSITION,
            "事实当前状态不允许该操作",
            detail={"current_state": fact.state, "action": action},
        )
    fact.state = new_state
    fact.revision += 1
    fact.updated_at = utcnow()
    session.flush()
    _emit_fact_event(
        session, event_type=EVENT_BY_STATE[new_state], fact=fact, actor_account_id=actor_account_id
    )
    return fact


def revise_source_fact(
    session: Session,
    fact: SourceFact,
    *,
    raw_text_id: int | None,
    actor_account_id: int | None = None,
) -> SourceFact:
    """内容变更（当前仅原文关联）：无变化拒绝；变更则 revision+1 并写 .revised。"""
    if fact.raw_text_id == raw_text_id:
        raise_api_error(422, VALIDATION_ERROR, "事实内容没有变化")
    fact.raw_text_id = raw_text_id
    fact.revision += 1
    fact.updated_at = utcnow()
    session.flush()
    _emit_fact_event(
        session, event_type=EVENT_REVISED, fact=fact, actor_account_id=actor_account_id
    )
    return fact


def _structural_fact_mapping(
    dir_class: str, from_user: int, to_user: int
) -> tuple[str, int, int] | None:
    """v1 结构边 → SourceFact 方向合同（v1：to_user 是 from_user 的 dir_class）。

    - elder f→t：t 是 f 长辈 → biological_parent(t, f)
    - younger f→t：f 是 t 长辈 → biological_parent(f, t)
    - spouse：对称 → spouse(f, t)
    peer 与非结构类不映射（返回 None）。
    """
    if dir_class == "elder":
        return "biological_parent", to_user, from_user
    if dir_class == "younger":
        return "biological_parent", from_user, to_user
    if dir_class == "spouse":
        return "spouse", from_user, to_user
    return None


def map_structural_edge_to_fact(
    session: Session,
    *,
    from_user: int,
    to_user: int,
    dir_class: str,
    asserted_by_account_id: int | None = None,
) -> SourceFact | None:
    """把已接受的 v1 结构边映射为 confirmed SourceFact（幂等，E1 生产入口）。

    血缘/配偶事实是全局事实（space_id=None），与 v1 结构边的全局信任代理语义一致；
    不因建立时是否携带空间成员意图而限定 scope。peer 边不映射。

    幂等：同类型非 revoked 事实已存在时，proposed/disputed 晋升 confirmed，
    已 confirmed 直接复用——不重复创建、不抛 duplicate（connection accept 可重入）。
    """
    mapping = _structural_fact_mapping(dir_class, from_user, to_user)
    if mapping is None:
        return None
    fact_type, subject_id, object_id = mapping
    existing = _find_active_duplicate(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        space_id=None,
    )
    if existing is not None:
        if existing.state in (FACT_PROPOSED, FACT_DISPUTED):
            transition_source_fact(
                session, existing, ACTION_CONFIRM, actor_account_id=asserted_by_account_id
            )
        return existing
    return create_source_fact(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        provenance="connection_accept",
        space_id=None,
        asserted_by_account_id=asserted_by_account_id,
        state=FACT_CONFIRMED,
    )


def revoke_structural_edge_fact(
    session: Session,
    *,
    from_user: int,
    to_user: int,
    dir_class: str,
    actor_account_id: int | None = None,
) -> None:
    """断开 v1 结构边时同步失效对应 SourceFact（AC-KI8：撤权后 DerivedFact/回答及时失效）。

    只 revoke confirmed/disputed；proposed 无法直接 revoke（FSM 无该转换），防御性跳过。
    """
    mapping = _structural_fact_mapping(dir_class, from_user, to_user)
    if mapping is None:
        return
    fact_type, subject_id, object_id = mapping
    existing = _find_active_duplicate(
        session,
        fact_type=fact_type,
        subject_user_id=subject_id,
        object_user_id=object_id,
        space_id=None,
    )
    if existing is not None and existing.state in (FACT_CONFIRMED, FACT_DISPUTED):
        transition_source_fact(
            session, existing, ACTION_REVOKE, actor_account_id=actor_account_id
        )
