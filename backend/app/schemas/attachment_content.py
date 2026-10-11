"""HTTP contracts for confirmed attachment content and Agent grants."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContentVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=12_000)
    content_kind: Literal["manual_text"] = "manual_text"


class ContentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attachment_id: int
    revision: int
    content_kind: str
    text: str
    content_sha256: str
    extractor_name: str
    extractor_version: str
    status: str
    created_by: int | None
    confirmed_by: int | None
    created_at: datetime
    confirmed_at: datetime | None
    superseded_at: datetime | None
    revoked_at: datetime | None


class ContentVersionConfirmOut(ContentVersionOut):
    pass


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int = Field(gt=0)
    agent_kind: Literal["assistant"] = "assistant"
    purpose: Literal["rag"] = "rag"
    grant_scope: Literal["whole"] = "whole"


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content_version_id: int
    space_id: int
    agent_kind: str
    purpose: str
    grant_scope: str
    grantor_user_id: int | None
    grantor_account_id: int | None
    authorization_revision: int
    status: str
    created_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None


class GrantRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=64)
