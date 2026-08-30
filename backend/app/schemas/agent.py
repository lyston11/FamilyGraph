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
    # The HTTP sidecar endpoint is assistant-only.  Steward jobs are leased by
    # the canonical in-process maintenance worker, never by a generic service
    # token caller.  Keeping this field required prevents an omitted kind from
    # becoming an accidental "any queue" lease.
    kind: Literal["assistant"] | None = "assistant"
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
    # Stable Pi provider name (for example ``liu-dada``); provider_id remains
    # the numeric DB/audit identifier and is retained for backwards compatibility.
    provider_name: str | None = None
    model: str | None
    kind: str | None
    api: str | None = None
    compat: dict[str, Any] = Field(default_factory=dict)
    context_window: int | None = None
    max_tokens: int | None = None
    reasoning: bool | None = None
    input_modalities: list[str] = Field(default_factory=list)
    thinking_levels: list[str] = Field(default_factory=list)
    policy_result: str
    secret_ref: str | None
    # ProviderGateway 注入的运行期配置（仅 internal listener；见 agent_provider.ProviderRuntime）
    base_url: str | None = None
    api_key: str | None = None


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
    # additive：预取的、带来源标记的安全 Context；context hook 不访问数据库
    context_build_id: int | None = None
    context_blocks: list[dict[str, Any]] = []
    # Next sidecar event sequence.  Runs may be re-leased after a crash; the
    # retry must continue after already persisted events rather than restarting
    # at seq=1 and colliding with a different response.
    next_event_seq: int = Field(default=1, ge=0)
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
    api: Literal["openai-completions", "openai-responses"] = "openai-responses"
    base_url: str | None = Field(default=None, max_length=500)
    compat: dict[str, Any] = Field(default_factory=dict)
    context_window: int = Field(default=272000, ge=1024, le=10_000_000)
    max_tokens: int = Field(default=60000, ge=16, le=1_000_000)
    reasoning: bool = True
    input_modalities: list[str] = Field(
        default_factory=lambda: ["text", "image"], min_length=1, max_length=4
    )
    thinking_levels: list[str] = Field(
        default_factory=lambda: ["low", "medium", "high", "xhigh", "max"],
        min_length=1,
        max_length=8,
    )
    # 只写不读：任何响应永不含明文或密文，仅返回 has_secret 布尔
    secret: str | None = Field(default=None, max_length=4096)
    allowed_models: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = True


class AgentProviderPatchRequest(_Strict):
    api: Literal["openai-completions", "openai-responses"] | None = None
    base_url: str | None = Field(default=None, max_length=500)
    compat: dict[str, Any] | None = None
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    max_tokens: int | None = Field(default=None, ge=16, le=1_000_000)
    reasoning: bool | None = None
    input_modalities: list[str] | None = Field(default=None, min_length=1, max_length=4)
    thinking_levels: list[str] | None = Field(default=None, min_length=1, max_length=8)
    secret: str | None = Field(default=None, max_length=4096)
    allowed_models: list[str] | None = Field(default=None, min_length=1, max_length=50)
    enabled: bool | None = None


class AgentProviderOut(BaseModel):
    id: int
    name: str
    kind: str
    api: str
    base_url: str | None
    compat: dict[str, Any]
    context_window: int
    max_tokens: int
    reasoning: bool
    input_modalities: list[str]
    thinking_levels: list[str]
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
