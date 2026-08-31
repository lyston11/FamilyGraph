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
    role: Literal["space_admin", "member", "guest"]
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


class EligibleManagerTarget(BaseModel):
    """可申请管理员的目标 lineage 空间（服务端裁定资格，前端只渲染）。"""

    model_config = ConfigDict(extra="forbid")

    space_id: int
    space_name: str
    space_kind: Literal["lineage"]
    current_manager_user_id: int | None = None
    current_manager_name: str | None = None
    has_pending_application: bool = False


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
    space_kind: Literal["household", "lineage"] | None = None
    current_manager_user_id: int | None = None
    current_manager_name: str | None = None
    transfer_consent_id: int | None = None
    transfer_consent_status: Literal["pending", "accepted", "rejected", "expired"] | None = None
    request_kind: Literal["space_admin"]
    status: Literal["pending", "approved", "rejected"]
    decision_note: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    system_admin_decided_by: int | None = None


class ManagerTransferConsentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject"]
    reason: str | None = Field(default=None, max_length=1000)


class ManagerTransferConsentOut(BaseModel):
    """原管理员工单投影。

    PRD R4：工单必须自带目标空间名称/类型和申请人标识，原管理员不需要再查一次
    空间就能判断"申请人正在申请成为哪一个空间的管理员"。名称由服务端按
    ``space_id`` 解析，不接受客户端自带值。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    space_id: int
    space_name: str | None = None
    space_kind: Literal["household", "lineage"] | None = None
    applicant_user_id: int | None = None
    applicant_name: str | None = None
    current_manager_user_id: int
    status: Literal["pending", "accepted", "rejected", "expired"]
    requested_at: datetime
    responded_at: datetime | None = None
    response_reason: str | None = None


class PositionItem(BaseModel):
    user_id: int = Field(gt=0)
    x: float
    y: float


class PositionsPayload(BaseModel):
    items: list[PositionItem]
