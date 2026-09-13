"""Schemas for the platform-level Memory/RAG capability switches."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StewardAssistSwitchesOut(BaseModel):
    """平台级 Steward 辅助三开关（09-13 治理；DB ∧ env 部署兜底）。"""

    candidate: bool
    ranking: bool
    explanation: bool
    candidate_source: Literal["environment", "platform", "deployment"]
    ranking_source: Literal["environment", "platform", "deployment"]
    explanation_source: Literal["environment", "platform", "deployment"]


class PlatformFeatureFlagsOut(BaseModel):
    memory_enabled: bool
    rag_enabled: bool


class PlatformFeatureAdminOut(PlatformFeatureFlagsOut):
    memory_source: Literal["environment", "platform", "deployment"]
    rag_source: Literal["environment", "platform", "deployment"]
    steward_assist: StewardAssistSwitchesOut
    updated_at: datetime | None


class PlatformFeatureUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_enabled: bool
    rag_enabled: bool
    # 省略（None）= 保留平台配置现值；行缺失时回退 env。全量治理 UI 显式传三值。
    steward_assist_candidate: bool | None = None
    steward_assist_ranking: bool | None = None
    steward_assist_explanation: bool | None = None
