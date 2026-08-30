"""家庭空间域 Pydantic 模型（m1c）。与前端 types/api.ts 人工同步。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["household", "lineage"] = "household"


class SpaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class SpaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner_id: int
    kind: str = "household"
    created_at: datetime
    pending_count: int = 0
    member_count: int = 0


class SpaceMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    space_id: int
    user_id: int
    user_name: str | None = None
    added_by: int | None
    role: Literal["owner", "space_admin", "member", "guest"]
    status: Literal["pending", "active", "rejected", "withdrawn", "removed"]
    updated_at: datetime


class SpaceProfileRefOut(BaseModel):
    """待确档最小节点引用（AC-F2 可观测性）：仅名字，无日期/简介/头像等字段。"""

    profile_id: int
    name: str
    added_at: datetime


class SpaceInviteCreate(BaseModel):
    user_id: int = Field(gt=0)


# ---- 空间管理者申请（任务 08-30-space-manager-approval）----


class ManagerApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: Literal["space_admin"] = "space_admin"
    space_id: int = Field(gt=0)


class ManagerApplicationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)


class ManagerApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    applicant_user_id: int
    applicant_name: str | None = None
    space_id: int
    space_name: str | None = None
    request_kind: Literal["space_admin"]
    status: Literal["pending", "approved", "rejected"]
    decision_note: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class PositionItem(BaseModel):
    user_id: int = Field(gt=0)
    x: float
    y: float


class PositionsPayload(BaseModel):
    items: list[PositionItem]
