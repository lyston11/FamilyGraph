"""V2 Foundation 新增端点的 Pydantic 模型（owner 邀请/移交/确档/数据权利/争议）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- owner onboarding ----


class OwnerInvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    expires_at: datetime
    used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class OwnerInvitationCreated(OwnerInvitationOut):
    """token 明文仅签发响应返回一次（服务端只存 hash）。"""

    token: str


class RedeemPayload(BaseModel):
    token: str = Field(min_length=16, max_length=200)


# ---- ownership transfer ----


class TransferCreate(BaseModel):
    to_user_id: int = Field(gt=0)


class TransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    space_id: int
    from_user: int
    to_user: int
    status: Literal["pending", "accepted", "cancelled", "expired"]
    created_at: datetime
    decided_at: datetime | None = None


# ---- identity / fact reviews ----


class IdentityConfirmResult(BaseModel):
    account_claimed: bool
    profile_confirmed: bool


class FactReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_type: str
    item_ref_json: dict[str, Any]
    status: Literal["proposed", "confirmed", "disputed"]
    decided_at: datetime | None = None
    created_at: datetime


class FactReviewDecision(BaseModel):
    decision: Literal["confirmed", "disputed"]
    note: str | None = Field(default=None, max_length=500)


# ---- data rights ----


class CorrectRequestPayload(BaseModel):
    fields: dict[str, Any] = Field(min_length=1)


class DataRightRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: Literal["export", "correct", "delete"]
    status: Literal["pending", "processing", "completed", "rejected", "expired"]
    scope: str
    policy_version: str
    payload_json: dict[str, Any] | None = None
    expires_at: datetime | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ClaimDisputeCreate(BaseModel):
    profile_id: int = Field(gt=0)
    evidence: dict[str, Any] = Field(min_length=1)


class ClaimDisputeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    raised_by_account_id: int
    evidence_json: dict[str, Any]
    status: Literal["open", "resolved_claim", "resolved_reject", "withdrawn"]
    resolution_note: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class OperatorResolveCorrection(BaseModel):
    approve: bool
    note: str = Field(min_length=1, max_length=1000)


class OperatorResolveDispute(BaseModel):
    outcome: Literal["resolved_claim", "resolved_reject"]
    note: str = Field(min_length=1, max_length=1000)
