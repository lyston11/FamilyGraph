"""系统管理员后台专用最小元数据投影。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdminAccountMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int
    subject_id: int
    subject_type: str
    status: str
    locked_until: datetime | None
    created_at: datetime


class SpaceManagerMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    space_name: str
    space_kind: str
    manager_user_id: int
    manager_account_id: int | None
    manager_name: str


class SpaceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    kind: str
    status: str
    created_at: datetime
    manager_user_id: int | None
    manager_account_id: int | None
    manager_name: str | None


class SpaceMemberMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    account_id: int | None
    name: str
    role: str
    status: str
    created_at: datetime
    updated_at: datetime


class TransferConsentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    application_id: int
    space_id: int
    space_name: str
    space_kind: str
    applicant_user_id: int
    applicant_name: str
    current_manager_user_id: int
    current_manager_name: str
    status: str
    requested_at: datetime
    responded_at: datetime | None
    response_reason: str | None
