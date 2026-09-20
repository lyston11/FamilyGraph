"""Household card API contract（字段白名单，前端 decoder 与此严格对齐）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.personal_family_view import VisibilityLevel


class HouseholdCardMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    display: dict[str, Any]
    household_label: str
    # viewer 视角的关系称谓，取自同一份已授权 PFV 投影；无授权路径时为 None
    # （不生成占位，不泄露「是否存在关系」）。
    relation_term: str | None = None
    visibility_level: VisibilityLevel


class HouseholdCardActionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_invite_members: bool
    can_create_household: bool
    empty_state_hint: str | None


class HouseholdCardOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    space_kind: Literal["household"]
    space_name: str
    view_version: int
    computed_at: datetime | None
    viewer: dict[str, Any]
    members: list[HouseholdCardMemberOut]
    allowed_actions: HouseholdCardActionsOut


__all__ = [
    "HouseholdCardActionsOut",
    "HouseholdCardMemberOut",
    "HouseholdCardOut",
]
