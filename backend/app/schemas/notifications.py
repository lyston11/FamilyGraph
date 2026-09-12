"""Notifications API contract（字段白名单，与前端 decoder 严格对齐）。

payload 字段允许明文字符串、``{"__masked__": true}`` 哨兵或 null；
domain_status 是跨领域 FSM 的最小状态集，ActionCard 引用只含 card_id/revision。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NotificationKind = Literal[
    "action_card", "space_membership", "bridge", "relation", "steward_suggestion"
]
NotificationDomainStatus = Literal[
    "pending",
    "active",
    "accepted",
    "rejected",
    "cancelled",
    "revoked",
    "expired",
    "withdrawn",
    "removed",
    "done",
]
MaskableString = str | dict[str, Any] | None


class NotificationPayloadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    summary: MaskableString
    actor_name: MaskableString
    space_name: MaskableString


class NotificationActionCardRefOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: int
    revision: int


class NotificationSuggestionRefOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion_id: int


class NotificationItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    space_id: int
    kind: NotificationKind
    payload: NotificationPayloadOut
    domain_status: NotificationDomainStatus
    action_card: NotificationActionCardRefOut | None
    suggestion: NotificationSuggestionRefOut | None
    created_at: datetime
    read_at: datetime | None


class NotificationsPageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    items: list[NotificationItemOut]
    unread_count: int


class NotificationReadAllIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int = Field(gt=0)


class NotificationReadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    read_at: datetime


class NotificationReadAllOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    marked_count: int


__all__ = [
    "NotificationActionCardRefOut",
    "NotificationDomainStatus",
    "NotificationItemOut",
    "NotificationKind",
    "NotificationReadAllIn",
    "NotificationReadAllOut",
    "NotificationReadOut",
    "NotificationSuggestionRefOut",
    "NotificationsPageOut",
    "NotificationPayloadOut",
]
