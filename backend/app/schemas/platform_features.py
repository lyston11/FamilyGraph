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
    terminology: bool
    candidate_source: Literal["environment", "platform", "deployment"]
    ranking_source: Literal["environment", "platform", "deployment"]
    explanation_source: Literal["environment", "platform", "deployment"]
    terminology_source: Literal["environment", "platform", "deployment"]


class PlatformFeatureFlagsOut(BaseModel):
    memory_enabled: bool
    rag_enabled: bool


class PlatformFeatureAdminOut(PlatformFeatureFlagsOut):
    memory_source: Literal["environment", "platform", "deployment"]
    rag_source: Literal["environment", "platform", "deployment"]
    steward_assist: StewardAssistSwitchesOut
    # 管家可读的记忆级别。设了什么与生效什么是两个值：
    # `steward_memory_scopes` = 平台列；`..._effective` = env ∩ 平台列。
    # 空串 = 空集 = 管家读不到记忆。
    steward_memory_scopes: str
    steward_memory_scopes_effective: str
    steward_memory_scopes_source: Literal["environment", "platform", "deployment"]
    updated_at: datetime | None


class PlatformFeatureUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_enabled: bool
    rag_enabled: bool
    # 省略（None）= 保留平台配置现值；行缺失时回退 env。全量治理 UI 显式传三值。
    steward_assist_candidate: bool | None = None
    steward_assist_ranking: bool | None = None
    steward_assist_explanation: bool | None = None
    steward_assist_terminology: bool | None = None
    # 省略（None）= 保留现值。非 None 时值域是 MEMORY_SCOPES 的子集；
    # 未知 scope 返回 422（不能静默丢弃——那会让“关掉”看起来生效了）。
    steward_memory_scopes: str | None = None
