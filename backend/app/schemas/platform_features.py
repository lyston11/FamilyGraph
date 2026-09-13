"""Schemas for the platform-level Memory/RAG capability switches."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PlatformFeatureFlagsOut(BaseModel):
    memory_enabled: bool
    rag_enabled: bool


class PlatformFeatureAdminOut(PlatformFeatureFlagsOut):
    memory_source: Literal["environment", "platform", "deployment"]
    rag_source: Literal["environment", "platform", "deployment"]
    updated_at: datetime | None


class PlatformFeatureUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_enabled: bool
    rag_enabled: bool
