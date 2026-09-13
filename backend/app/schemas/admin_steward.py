"""Admin Steward 运维 API schema（09-11；仅 admin listener :8002）。

响应字段白名单（AC-5）：作业元数据只含 job_id/space_id/cause/status/attempt/
available_at/error_code —— 绝不含 checkpoint、error_json、事件窗口、领域事实、
prompt 或任何家庭内容（系统管理员是独立主体，元数据诊断不授予家庭数据权）。
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StewardJobOut(BaseModel):
    """单个作业的运维元数据（字段白名单，测试用精确集合断言）。"""

    model_config = ConfigDict(from_attributes=True)

    job_id: int
    space_id: int
    cause: str
    status: str
    attempt: int
    available_at: datetime | None = None
    error_code: str | None = None


class StewardJobsPageOut(BaseModel):
    items: list[StewardJobOut]
    page: int
    page_size: int


class StewardRerunRequest(BaseModel):
    """人工重跑请求：必须给出原因与期望策略版本；reason 原文永不落库/审计。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    expected_policy_version: str = Field(min_length=1, max_length=32)


class StewardRerunAccepted(BaseModel):
    job_id: int
    coalesced: bool


class StewardSwitchStateOut(BaseModel):
    """单个开关的有效状态与可解释说明（AC-1：任意组合均可解释）。"""

    key: str
    enabled: bool
    explanation: str


class StewardMetricsOut(BaseModel):
    """运行观测指标（09-11 发布可观测性 R2；全部来自真实 DB/worker 状态）。

    只含计数、时间戳与安全错误码 —— 绝无人物、事实、prompt、错误原文。
    """

    core_queue_depth: int
    oldest_queued_age_seconds: int | None = None
    last_scan_at: datetime | None = None
    last_worker_tick_at: datetime | None = None
    core_failed: int = 0
    assist_failed: int = 0
    assist_degraded: int = 0
    assist_unknown: int = 0
    budget_reserved_tokens: int = 0
    budget_consumed_tokens: int = 0
    pfv_stale: int = 0
    cards_created: int = 0
    cards_superseded: int = 0


class StewardAlertOut(BaseModel):
    """可见告警（只报告，不自动处置）。code 白名单：queue_backlog / queue_stalled。"""

    code: Literal["queue_backlog", "queue_stalled"]
    detail: dict[str, int | str | None] = Field(default_factory=dict)


class StewardStatusOut(BaseModel):
    """Steward 运行状态（只读；不依赖引擎启用门禁）。"""

    state: Literal["disabled", "paused", "running", "degraded"]
    switches: list[StewardSwitchStateOut]
    worker_heartbeat_at: datetime | None = None
    queue_counts: dict[str, int]
    oldest_queued_seconds: int | None = None
    recent_error_codes: list[str]
    metrics: StewardMetricsOut
    alerts: list[StewardAlertOut] = []
    # 09-13 R6/R8：核心发布完成与后续交付分开表达
    delivery_backlog: int | None = None
    latest_generation: dict[str, Any] | None = None
