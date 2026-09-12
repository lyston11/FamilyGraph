"""领域事件写入唯一入口（v2 Foundation §0.6）。

domain_events 为 append-only 稳定事实流：删除/撤权/争议传播经此驱动
缓存、附件、DerivedFact、RAG/搜索索引与 Agent 会话投影失效。由调用方
事务统一提交。

PFV 影响解析（09-11 projection-consistency R1）：`resolve_pfv_impact`
按真实事件类型精确解析受影响 (space_id, viewer_account_id) 集合——

- 带空间维度的事件（space.*、space_profile_ref.*、term.space_*、bridge）直接
  取事件/负载里的空间；
- 全局人物事件（全局 source_fact、disclosure、profile、relation）按该人物的
  active membership / active profile ref（及经 active 桥接关联的空间）定位，
  绝不把所有无空间事件无差别全局 fan-out；
- 私人 memory./rag. 事件不触发任何空间失效或 Steward 工作。

同一入口还负责：成员资格合法获得（注册建空间/码加入/邀请接受）时为该账号
后台初始化 queued 视图行（R2），并在同事务合并 Steward 队列水位。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.v2_foundation import DomainEvent
from app.utils.timeutil import utcnow

_INTERNAL_STEWARD_EVENT_PREFIXES = ("card.", "steward.")

# 可能影响 PersonalFamilyView 投影的事件前缀；memory./rag./message./agent 等
# 私人或运行时事件一律不在内（R1：不得无差别全局 fan-out）。
_PFV_EVENT_PREFIXES = (
    "source_fact.",
    "space.",
    "space_profile_ref.",
    "relation.",
    "term.",
    "disclosure.",
    "profile.",
    "personal_family_bridge.",
)

# 成员资格合法获得的动作值（触发 R2 后台初始化）；其余动作是失去/等待。
_MEMBERSHIP_GAIN_ACTIONS = ("accepted", "household_link_activated")


def _spaces_for_user(session: Session, user_id: int) -> set[int]:
    """该人物当前被授权可见/参与的空间集合（member ∪ ref ∪ 桥接关联空间）。

    全局人物事件按此集合收敛影响面；桥接把对端空间成员拉入本空间可见集，
    因此一端空间变化也要失效对端经桥接的投影。
    """
    from app.models.personal_family_view import PersonalFamilyBridge
    from app.models.space import SpaceMember, SpaceProfileRef

    spaces: set[int] = {
        int(space_id)
        for space_id in session.scalars(
            select(SpaceMember.space_id).where(
                SpaceMember.user_id == user_id, SpaceMember.status == "active"
            )
        ).all()
    }
    spaces.update(
        int(space_id)
        for space_id in session.scalars(
            select(SpaceProfileRef.space_id).where(
                SpaceProfileRef.user_id == user_id, SpaceProfileRef.status == "active"
            )
        ).all()
    )
    if spaces:
        bridges = session.scalars(
            select(PersonalFamilyBridge).where(
                PersonalFamilyBridge.status == "active",
                or_(
                    PersonalFamilyBridge.lineage_space_a_id.in_(spaces),
                    PersonalFamilyBridge.lineage_space_b_id.in_(spaces),
                ),
            )
        ).all()
        for bridge in bridges:
            spaces.add(int(bridge.lineage_space_a_id))
            spaces.add(int(bridge.lineage_space_b_id))
    return spaces


def _event_user_ids(event: DomainEvent, payload: dict[str, Any]) -> set[int]:
    """从事件负载/聚合中提取人物 id（按生产者真实 payload 字段采样）。"""
    user_ids: set[int] = set()
    for key in (
        "subject_user_id",
        "object_user_id",
        "user_id",
        "profile_id",
        "from_user",
        "to_user",
    ):
        value = payload.get(key)
        if isinstance(value, int):
            user_ids.add(value)
    for key in ("user_ids",):
        values = payload.get(key)
        if isinstance(values, list):
            user_ids.update(v for v in values if isinstance(v, int))
    if event.aggregate_type == "profile" and isinstance(event.aggregate_id, int):
        user_ids.add(event.aggregate_id)
    return user_ids


def resolve_pfv_impact(session: Session, event: DomainEvent) -> dict[int, set[int | None]]:
    """解析事件 → 受影响空间及账号范围。

    返回 {space_id: {viewer_account_id 或 None}}；None 表示该空间全部视图。
    非受影响事件返回空 dict。term.personal_updated 只命中本人账号的视图。
    """
    if not event.type.startswith(_PFV_EVENT_PREFIXES):
        return {}
    payload = event.payload or {}
    scopes: dict[int, set[int | None]] = {}

    def add(space_id: int, account_id: int | None = None) -> None:
        scopes.setdefault(int(space_id), set()).add(account_id)

    event_type = event.type
    if event_type.startswith("term.personal"):
        # 个人偏好：影响同账号全部相关空间，绝不波及其他账号的视图。
        account_id = payload.get("account_id")
        if isinstance(account_id, int):
            user_id = session.scalar(select(Account.user_id).where(Account.id == account_id))
            if user_id is not None:
                for space_id in _spaces_for_user(session, int(user_id)):
                    add(space_id, account_id)
        return scopes

    space_ids: set[int] = set()
    if isinstance(event.space_id, int):
        space_ids.add(event.space_id)
    payload_space_ids = payload.get("space_ids")
    if isinstance(payload_space_ids, list):
        space_ids.update(s for s in payload_space_ids if isinstance(s, int))
    payload_space_id = payload.get("space_id")
    if isinstance(payload_space_id, int):
        space_ids.add(payload_space_id)

    if not space_ids:
        # 无空间维度的人物事件：按人物 active membership/ref/桥接受权范围收敛。
        for user_id in _event_user_ids(event, payload):
            space_ids |= _spaces_for_user(session, user_id)

    for space_id in space_ids:
        add(space_id)
    return scopes


def _maybe_initialize_views(event: DomainEvent, session: Session) -> None:
    """R2：成员资格合法获得/建空间后在同事务初始化该账号的 queued 视图行。

    只为持有 Account 的 active 目标建行；仅被 provisional 引用、没有自己
    Account 的人物绝不伪造视图。
    """
    from app.services.personal_family_view import initialize_account_views

    payload = event.payload or {}
    user_ids: set[int] = set()
    if event.type == "space.created":
        owner_id = payload.get("owner_id")
        if isinstance(owner_id, int):
            user_ids.add(owner_id)
    elif event.type == "space.membership.changed":
        if payload.get("action") in _MEMBERSHIP_GAIN_ACTIONS:
            user_id = payload.get("user_id")
            if isinstance(user_id, int):
                user_ids.add(user_id)
            listed = payload.get("user_ids")
            if isinstance(listed, list):
                user_ids.update(v for v in listed if isinstance(v, int))
    for user_id in user_ids:
        account_id = session.scalar(select(Account.id).where(Account.user_id == user_id))
        if account_id is not None:
            initialize_account_views(session, account_id=int(account_id), user_id=user_id)


def _invalidate_personal_family_view(event: DomainEvent, session: Session) -> None:
    scopes = resolve_pfv_impact(session, event)
    if not scopes:
        return
    from app.services.personal_family_view import invalidate_view_scopes

    invalidate_view_scopes(session, scopes=scopes)
    _maybe_initialize_views(event, session)


def _schedule_steward_job(event: DomainEvent, session: Session) -> None:
    """在领域事件所属事务内登记 Steward 水位，避免提交后丢触发。"""
    if event.space_id is None and (
        event.type.startswith("memory.") or event.type.startswith("rag.")
    ):
        return
    if not event.type.startswith(_INTERNAL_STEWARD_EVENT_PREFIXES):
        # Import lazily: steward imports this module to append its own events.
        from app.services.steward import schedule_steward_job_for_event

        schedule_steward_job_for_event(session, event)


def _apply_rag_invalidation(event: DomainEvent, session: Session) -> None:
    # Keep the dependency lazy: memory_rag records events through this module.
    from app.services.memory_rag import invalidate_for_domain_event

    invalidate_for_domain_event(
        session,
        event_type=event.type,
        aggregate_id=event.aggregate_id,
        payload=event.payload or {},
    )


def emit(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: int,
    payload: dict[str, Any] | None = None,
    space_id: int | None = None,
    actor_account_id: int | None = None,
) -> DomainEvent:
    """追加一条领域事件；单调 id 由自增主键保证。禁止 UPDATE/DELETE。"""
    event = DomainEvent(
        type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload or {},
        space_id=space_id,
        actor_account_id=actor_account_id,
        created_at=utcnow(),
    )
    session.add(event)
    _apply_rag_invalidation(event, session)
    _invalidate_personal_family_view(event, session)
    _schedule_steward_job(event, session)
    return event
