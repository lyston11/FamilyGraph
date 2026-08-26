"""领域事件写入唯一入口（v2 Foundation §0.6）。

domain_events 为 append-only 稳定事实流：删除/撤权/争议传播经此驱动
缓存、附件、DerivedFact、RAG/搜索索引与 Agent 会话投影失效（投影本体
在后续任务实现，本模块只定义事件合同）。由调用方事务统一提交。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.v2_foundation import DomainEvent
from app.utils.timeutil import utcnow

_INTERNAL_STEWARD_EVENT_PREFIXES = ("card.", "steward.")


def _schedule_steward_job(event: DomainEvent, session: Session) -> None:
    """在领域事件所属事务内登记 Steward 水位，避免提交后丢触发。"""
    if not event.type.startswith(_INTERNAL_STEWARD_EVENT_PREFIXES):
        # Import lazily: steward imports this module to append its own events.
        from app.services.steward import schedule_steward_job_for_event

        schedule_steward_job_for_event(session, event)


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
    _schedule_steward_job(event, session)
    return event
