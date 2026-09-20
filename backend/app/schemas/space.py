"""家庭空间域 Pydantic 模型（m1c）。与前端 types/api.ts 人工同步。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2_foundation import TransferOut

SpaceRole = Literal["space_admin", "member"]


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["household", "lineage"] = "household"
    # 创建时直接挂到家族空间（仅 household 可用；须为该 lineage active 成员）
    lineage_space_id: int | None = Field(default=None, gt=0)


class SpaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class SpaceLineageLinkUpdate(BaseModel):
    """家庭空间 ↔ 家族空间显式配对（ lineage_space_id=null 表示解除配对）。"""

    model_config = ConfigDict(extra="forbid")

    lineage_space_id: int | None = Field(default=None, gt=0)


class SpaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner_id: int
    kind: str = "household"
    lineage_space_id: int | None = None
    created_at: datetime
    pending_count: int = 0
    member_count: int = 0
    # 当前认证账号在该空间的能力投影；不作为后端授权依据。
    current_role: SpaceRole | None = None
    # 当前认证账号在该空间的成员行 id（退出入口复用 DELETE /space-memberships/{id}）；
    # 只在「我的空间」列表投影里填充，其他构造点保持 None。
    my_member_id: int | None = None


class FamilySpaceOptionOut(BaseModel):
    """家族空间下的一个家庭空间 + 指定一方在该空间的状态。

    ``status`` 只区分三态：``active`` 已是成员、``pending`` 已有待处理申请/
    邀请、其余（含 rejected/withdrawn/removed）一律 ``none``——终态行可以再次
    邀请/申请，与 space_fsm.invite 的复活语义一致。
    """

    space_id: int
    space_name: str
    status: Literal["active", "pending", "none"]


class FamilySpaceOptionsOut(BaseModel):
    """个人公示页在当前家族空间下的双向选择（只读投影）。

    - ``shares_lineage=false``：双方不同族，两个方向都不可用（走邀请码途径）；
    - ``invite``：我在该家族空间下的家庭空间 + 目标在各自空间的状态；
    - ``join``：对方在该家族空间下的家庭空间 + 我在各自空间的状态。
    """

    lineage_space_id: int
    lineage_space_name: str
    shares_lineage: bool
    invite: list[FamilySpaceOptionOut]
    join: list[FamilySpaceOptionOut]


class SpaceMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    space_id: int
    user_id: int
    user_name: str | None = None
    added_by: int | None
    role: SpaceRole
    status: Literal["pending", "active", "rejected", "withdrawn", "removed"]
    updated_at: datetime


class SpaceProfileRefOut(BaseModel):
    """待确档最小节点引用（AC-F2 可观测性）：仅名字，无日期/简介/头像等字段。"""

    profile_id: int
    name: str
    added_at: datetime


class SpaceManagementBootstrapOut(BaseModel):
    """空间管理页首屏授权与数据快照。"""

    model_config = ConfigDict(extra="forbid")

    space: SpaceOut
    members: list[SpaceMemberOut]
    transfers: list[TransferOut]
    profile_refs: list[SpaceProfileRefOut]


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


# ---- 同一空间重复人物处置（任务 09-01-person-identity-dedupe）----


class DuplicatePairOut(BaseModel):
    """疑似重复对的最小元数据：只含 id/姓名/生日有无，不含 bio/关系/附件等敏感字段。"""

    user_ids: list[int]
    strength: Literal["strong", "weak"]
    names: list[str]
    birth_known_flags: list[bool]


class DuplicatePeopleMergeRequest(BaseModel):
    """合并是显式两步确认操作：confirm_same_person 必须显式传 true。"""

    model_config = ConfigDict(extra="forbid")

    survivor_user_id: int = Field(gt=0)
    retired_user_id: int = Field(gt=0)
    confirm_same_person: bool = False


class DuplicatePeopleMergeOut(BaseModel):
    survivor_user_id: int
    retired_user_id: int
    moved: dict[str, int]
    already_merged: bool
