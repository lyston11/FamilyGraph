"""Internal Agent 协议与浏览器 Agent API 的请求/响应模型。

输入模型一律 extra="forbid"（fail-closed：额外字段拒绝）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app import config


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- lease ----


class LeaseRequest(_Strict):
    # kind=None 表示任意队列：跨队列按 created_at FIFO（assistant 仅作确定性排序）
    kind: Literal["assistant", "steward"] | None = None
    leased_by: str = Field(min_length=1, max_length=120)
    lease_ttl_seconds: int | None = Field(default=None, ge=30, le=3600)


class LeaseOut(BaseModel):
    job_id: int
    run_id: int
    agent_kind: str
    attempt: int
    tool_allowlist: list[str]
    policy_version: str
    run_token: str


# ---- heartbeat ----


class HeartbeatRequest(_Strict):
    lease_ttl_seconds: int | None = Field(default=None, ge=30, le=3600)


class HeartbeatOut(BaseModel):
    ok: Literal[True]
    lease_expires_at: datetime
    # additive：浏览器已请求取消时为 true（B2 客户端兼容未知字段）
    cancel_requested: bool = False


# ---- context ----


class ContextMessageOut(BaseModel):
    id: int
    role: str
    content_json: dict[str, Any]
    created_at: datetime


class ContextProviderOut(BaseModel):
    provider_id: int | None
    model: str | None
    kind: str | None
    policy_result: str
    secret_ref: str | None


class ContextOut(BaseModel):
    run_id: int
    session_id: int
    agent_kind: str
    account_id: int
    space_id: int
    status: str
    attempt: int
    policy_version: str
    tool_allowlist: list[str]
    messages: list[ContextMessageOut]
    provider: ContextProviderOut | None
    # additive：浏览器已请求取消（同 heartbeat）
    cancel_requested: bool = False


# ---- events ----


class EventIn(_Strict):
    seq: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=64)
    public_payload: dict[str, Any]


class EventAppendRequest(_Strict):
    events: list[EventIn] = Field(min_length=1, max_length=100)


class EventAcceptedOut(BaseModel):
    seq: int
    event_id: int


class EventAppendOut(BaseModel):
    accepted: list[EventAcceptedOut]
    duplicates: list[int]


# ---- tools ----


class ToolExecuteRequest(_Strict):
    version: int = Field(ge=1)
    input: dict[str, Any] = Field(default_factory=dict)
    # 透传元数据：记录进工具执行审计；副作用去重表在首个写工具落地时实现（V2.4）
    tool_call_id: str | None = Field(default=None, max_length=128)


class ToolExecuteOut(BaseModel):
    ok: Literal[True]
    tool: str
    version: int
    output: dict[str, Any]


# ---- settle ----


class SettleRequest(_Strict):
    status: Literal["succeeded", "failed"]
    error_code: str | None = Field(default=None, max_length=64)
    error: dict[str, Any] | None = None


class SettleOut(BaseModel):
    ok: Literal[True]
    run_id: int
    status: str
    settled_at: datetime


# ---- 浏览器 Agent API（api/agent.py，RT-4）----


class AgentSessionCreateRequest(_Strict):
    space_id: int = Field(ge=1)


class AgentSessionOut(BaseModel):
    id: int
    space_id: int
    agent_kind: str
    created_at: datetime


class AgentMessageCreateRequest(_Strict):
    content: str = Field(min_length=1, max_length=config.AGENT_MESSAGE_MAX_LENGTH)


class AgentMessageOut(BaseModel):
    """历史投影：不含 idempotency_key 等系统内部字段。"""

    id: int
    role: str
    content_json: dict[str, Any]
    created_at: datetime


class AgentRunRefOut(BaseModel):
    id: int
    status: str
    attempt: int
    cancel_requested: bool


class AgentMessageCreatedOut(BaseModel):
    message: AgentMessageOut
    run: AgentRunRefOut | None
    replayed: bool


class AgentRunOut(BaseModel):
    id: int
    session_id: int
    kind: str
    status: str
    attempt: int
    max_attempts: int
    cancel_requested: bool
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    settled_at: datetime | None


# ---- Provider 治理（platform_operator 专用，api/admin_agent.py，RT-5）----


class AgentProviderCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["openai_compatible", "local"]
    base_url: str | None = Field(default=None, max_length=500)
    # 只写不读：任何响应永不含明文或密文，仅返回 has_secret 布尔
    secret: str | None = Field(default=None, max_length=4096)
    allowed_models: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = True


class AgentProviderPatchRequest(_Strict):
    base_url: str | None = Field(default=None, max_length=500)
    secret: str | None = Field(default=None, max_length=4096)
    allowed_models: list[str] | None = Field(default=None, min_length=1, max_length=50)
    enabled: bool | None = None


class AgentProviderOut(BaseModel):
    id: int
    name: str
    kind: str
    base_url: str | None
    has_secret: bool
    allowed_models: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentSpaceProviderSettingsRequest(_Strict):
    """空间级 Provider 选择与开关；provider_id=None 表示清除该空间选择。"""

    provider_id: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    cloud_allowed: bool = False
    local_required: bool = False


class AgentSpaceProviderSettingsOut(BaseModel):
    space_id: int
    provider_id: int | None
    model: str | None
    cloud_allowed: bool
    local_required: bool
    enabled: bool
