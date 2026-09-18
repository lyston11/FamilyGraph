"""Internal Agent 协议与浏览器 Agent API 的请求/响应模型。

输入模型一律 extra="forbid"（fail-closed：额外字段拒绝）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import config
from app.models.agent import RuntimeAgentKind as AgentKind


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- lease ----


class LeaseRequest(_Strict):
    # The HTTP sidecar endpoint is assistant-only. Steward jobs are handled by
    # the canonical in-process maintenance worker, never by a generic service
    # token caller. This required field prevents omitted kind from becoming an
    # accidental queue selector.
    kind: Literal["assistant"]
    leased_by: str = Field(min_length=1, max_length=120)
    lease_ttl_seconds: int | None = Field(default=None, ge=30, le=3600)


class LeaseOut(BaseModel):
    job_id: int
    run_id: int
    agent_kind: AgentKind
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
    agent_kind: AgentKind
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


class ContextReferenceIn(_Strict):
    build_id: int = Field(strict=True, ge=1)
    attempt: int = Field(strict=True, ge=1)
    used_handles: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(max_length=20)


# 有界内部计时记录（09-17 D）。
# ``agent_run_events.created_at`` 是后端入库时刻，sidecar 默认每 250ms 批量 flush，
# 因此同批入库的 125ms 工具执行与其结束事件只差约 1ms。该结构承载 producer 侧的
# 单调时长，与 ``context_reference`` 同模式：只存数字与来源标记，永不进入
# ``public_payload``，也永不记录 prompt/正文/thinking/工具结果。
MAX_TIMING_MS = 86_400_000  # 24h：超出即畸形上报

# 允许携带 producer 计时的 sidecar 事件类型（注册表子集）：每个事件承载的是
# **在该事件处结束的那个阶段**的时长（run.started=准备、assistant 正文=该轮生成、
# 工具完成=该次工具执行）。
_SIDECAR_TIMED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run.started",
        "message.assistant_added",
        "tool.execution.completed",
    }
)


class EventTimingIn(_Strict):
    """producer 侧阶段计时；单位毫秒，单调测量，不跨进程相减。

    ``duration_ms`` 是本事件处结束的那个阶段的时长：

    - ``run.started``：取得执行权 → SDK ``agent_start``（context 获取 + session
      创建）。它解释“取得执行权 → SDK 开始”的间隔，不能算作排队等待；
    - ``message.assistant_added``：该轮 ``turn_start`` → ``message_end``；
    - ``tool.execution.completed``：该次 ``tool_execution_start`` → ``end``。

    历史行无该记录（NULL），读取方按 unknown 处理，不用持久事件间隔冒充精确耗时。
    """

    source: Literal["sidecar-v1"]
    duration_ms: int = Field(strict=True, ge=0, le=MAX_TIMING_MS)
    # 该轮内 SDK 压缩（compaction_start→end）的累计时长；它是 ``duration_ms``
    # 的**子成分**（压缩请求发生在 turn 内），与 provider_retry 一样必须单独读，
    # 不能把 model_turn 直接称为纯生成。无压缩即缺省（None），不写 0。
    compaction_ms: int | None = Field(default=None, strict=True, ge=0, le=MAX_TIMING_MS)

    @model_validator(mode="after")
    def check_subcomponents(self) -> EventTimingIn:
        # 子成分不可能超过它所归属的阶段：越界说明两侧语义漂移，拒绝而不是
        # 静默夹紧（夹紧会把畸形数据伪装成合法测量）。
        if self.compaction_ms is not None and self.compaction_ms > self.duration_ms:
            raise ValueError("compaction_ms 不能超过 duration_ms")
        return self


class EventIn(_Strict):
    seq: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=64)
    public_payload: dict[str, Any]
    context_reference: ContextReferenceIn | None = None
    timing: EventTimingIn | None = None

    @model_validator(mode="after")
    def check_reference_type(self) -> EventIn:
        if self.context_reference is not None and self.type != "message.assistant_added":
            raise ValueError("context_reference 仅用于完成的 assistant 消息")
        return self

    @model_validator(mode="after")
    def check_timing_type(self) -> EventIn:
        # 计时是 sidecar 的执行证据；后端自有事件（入队/终态）不得携带，
        # 否则会把服务端入库时刻伪装成 producer 测量。
        if self.timing is not None and self.type not in _SIDECAR_TIMED_EVENT_TYPES:
            raise ValueError("timing 仅用于 sidecar 执行事件")
        # 压缩是轮内子成分，只在正文事件上有意义。
        if (
            self.timing is not None
            and self.timing.compaction_ms is not None
            and self.type != "message.assistant_added"
        ):
            raise ValueError("compaction_ms 仅用于 assistant 正文事件")
        return self


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


class AgentSessionRenameRequest(_Strict):
    title: str = Field(min_length=1, max_length=120)


class AgentSessionOut(BaseModel):
    id: int
    space_id: int
    agent_kind: AgentKind
    created_at: datetime
    title: str | None = None
    updated_at: datetime


class AgentMessageCreateRequest(_Strict):
    content: str = Field(min_length=1, max_length=config.AGENT_MESSAGE_MAX_LENGTH)


class CitationOut(BaseModel):
    """最小引用元数据合同（六字段；无摘录正文）。"""

    source_type: str
    source_id: str
    scope: str
    sensitivity: str
    revision: int
    citation_handle: str
    document_id: int | None = None
    chunk_id: int | None = None
    index_version: str | None = None


class AgentMessageOut(BaseModel):
    """历史投影：不含 idempotency_key 等系统内部字段。

    citations 只包含当前读者仍可读的来源；unavailable_citation_count 表达
    因来源失效/失权而不在 citations 中的引用数量（可选，旧消息缺省 0）。
    """

    id: int
    role: str
    content_json: dict[str, Any]
    created_at: datetime
    citations: list[CitationOut] = []
    unavailable_citation_count: int = 0


class RunEventCitationsOut(BaseModel):
    """引用固定后备读取（GET /runs/{id}/events/{seq}/citations）。"""

    run_id: int
    seq: int
    citations: list[CitationOut]
    unavailable_citation_count: int


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
    kind: AgentKind
    status: str
    attempt: int
    max_attempts: int
    cancel_requested: bool
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    settled_at: datetime | None


# ---- Provider 治理（系统管理员域，api/admin_agent.py 仅 admin_app :8002）----


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


# ---- 家庭域空间模型设置共享投影（owner 侧 api/space_model_settings.py + 管理员只读视图）----


class SpaceAgentSettingOut(BaseModel):
    """单 agent 维度的空间行级设置；enabled=False 且 provider/model 为空 = 显式停用。

    assist_* 三开关仅 steward 维度消费（09-06 模型辅助层；assistant 行恒 False）。
    """

    agent_kind: str
    provider_id: int | None
    model: str | None
    cloud_allowed: bool
    local_required: bool
    enabled: bool
    assist_candidate: bool = False
    assist_ranking: bool = False
    assist_explanation: bool = False
    assist_terminology: bool = False
    # 09-13 治理：辅助开关生效值（平台配置 ∧ 空间级；平台未开启时家庭端
    # 据此显示可解释提示，不暴露 env 细节）
    assist_candidate_effective: bool = False
    assist_ranking_effective: bool = False
    assist_explanation_effective: bool = False
    assist_terminology_effective: bool = False
    # 09-13 推测层：空间级开关 + 有效开关（平台 AND 空间；平台未开启时前端
    # 据此显示可解释提示，不暴露 env 细节）
    inferred_tree: bool = False
    inferred_effective: bool = False


class SpaceModelSettingsKindsOut(BaseModel):
    """两 agent 维度的行级设置投影；None = 无显式行（继承平台默认）。"""

    assistant: SpaceAgentSettingOut | None = None
    steward: SpaceAgentSettingOut | None = None


class AgentModelCatalogEntryOut(BaseModel):
    """管理员允许目录条目（仅 enabled Provider；无任何密钥形态字段）。"""

    provider_id: int
    name: str
    kind: str
    api: str
    models: list[str]


class AgentPlatformDefaultKindOut(BaseModel):
    """单 agent 维度的平台默认（成对出现；provider 删除时 SET NULL 整对失效）。"""

    provider_id: int
    model: str


class AgentPlatformDefaultsOut(BaseModel):
    """平台默认投影：None = 该维度未设默认（空间解析回退 no_space_setting）。"""

    assistant: AgentPlatformDefaultKindOut | None = None
    steward: AgentPlatformDefaultKindOut | None = None
    updated_at: datetime | None = None


class SpaceModelSettingsOut(BaseModel):
    """owner 侧模型设置视图：行级设置 + 可选目录 + 有效平台默认（解析同口径）。"""

    space_id: int
    settings: SpaceModelSettingsKindsOut
    catalog: list[AgentModelCatalogEntryOut]
    platform_default: AgentPlatformDefaultsOut


class AdminSpaceProviderSettingsOut(BaseModel):
    """管理员域空间设置只读排查视图（原始存储态，不代替 owner 选择；design §3.1）。

    settings 任一维度为 None 表示该空间该维度无显式行（当前继承平台默认）。
    """

    space_id: int
    settings: SpaceModelSettingsKindsOut
    platform_default: AgentPlatformDefaultsOut


# ---- 平台默认与空间模型设置请求 ----


class AgentPlatformDefaultKindRequest(_Strict):
    provider_id: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=120)


class AgentPlatformDefaultsRequest(_Strict):
    """PUT 全量覆盖：字段缺省/显式 null 均表示清除该维度默认。"""

    assistant: AgentPlatformDefaultKindRequest | None = None
    steward: AgentPlatformDefaultKindRequest | None = None


class AgentSpaceModelSettingsRequest(_Strict):
    """owner 侧单维度模型选择（PUT 幂等 upsert）。

    - enabled=true：provider_id 与 model 必填且 model ∈ Provider allowlist；
    - enabled=false：显式停用（provider/model 可空）→ 解析走 setting_disabled；
    - 继承平台默认请用 DELETE（enabled=true 且无 provider/model 一律 422）。
    """

    agent_kind: Literal["assistant", "steward"]
    provider_id: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    cloud_allowed: bool = False
    local_required: bool = False
    enabled: bool = True
    # 模型辅助层空间级开关（仅 steward 维度；assistant 维度任一非 None → 422）
    assist_candidate: bool | None = None
    assist_ranking: bool | None = None
    assist_explanation: bool | None = None
    assist_terminology: bool | None = None
    # 推测层空间级开关（仅 steward 维度；assistant 维度非 None → 422）
    inferred_tree: bool | None = None
