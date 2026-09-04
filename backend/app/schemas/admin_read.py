"""/admin-api/v1 专用响应 schema（09-04 子任务 2 字段白名单）。

每个响应都是显式字段的专用投影（``extra="forbid"``），绝不直接序列化 ORM：
- AdminProfileOut 只含 id/name/gender/birth/death/bio/avatar_available/
  profile_status/claim_status/created_at（User 无 updated_at，不得虚构）；
- AdminAttachmentMetadataOut 只含 id/type/title_safe/created_at（Attachment
  无 size；url_or_path/description/下载 URL 永不出现）;
- Agent 投影只含状态/kind/attempt/timing/lease/error_code/资源 ID 与经
  admin_sanitizer 二次脱敏的诊断（error_json/result_json 原文永不直接序列化）。

新增字段必须先登记字段分类并同步更新 tests/test_admin_read_model.py 的
精确集合断言（spec/backend/quality-guidelines.md）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.relationship_facts import SOURCE_FACT_TYPES

T = TypeVar("T")

PAGE_SIZE_MAX = 100


class AdminPageOut(BaseModel, Generic[T]):
    """统一列表 envelope：{items, page, page_size, total, has_more}。"""

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    page: int
    page_size: int
    total: int
    has_more: bool


# ---- 聚合根：space_admin → managed spaces ----


class AdminSpaceAdminOut(BaseModel):
    """空间管理员聚合行（同一用户在不同空间分别聚合，space_count 为其管理数）。"""

    model_config = ConfigDict(extra="forbid")

    admin_user_id: int
    name: str
    gender: str
    profile_status: Literal["provisional", "identity_confirmed"]
    account_status: Literal["managed", "claimed"]
    avatar_available: bool
    space_count: int
    created_at: datetime


class AdminSpaceSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    name: str
    kind: Literal["household", "lineage"]
    created_at: datetime
    manager_user_id: int | None = None
    manager_name: str | None = None


class AdminSpaceDetailOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    name: str
    kind: Literal["household", "lineage"]
    created_at: datetime
    manager_user_id: int | None = None
    manager_name: str | None = None
    member_count: int
    anomalies: list[str]


class AdminOverviewTotalsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spaces_total: int
    healthy_spaces: int
    anomaly_spaces: int
    active_space_admins: int
    pending_applications: int


class AdminOverviewItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    name: str
    kind: Literal["household", "lineage"]
    created_at: datetime
    manager_user_id: int | None = None
    manager_name: str | None = None
    member_count: int
    status: Literal["healthy", "anomaly"]
    anomalies: list[str]


class AdminOverviewPageOut(AdminPageOut[AdminOverviewItemOut]):
    """overview：分页列表 + 全局统计头。"""

    totals: AdminOverviewTotalsOut


# ---- 空间成员 / 档案 / 附件 ----


class AdminMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    name: str
    role: Literal["space_admin", "member"]
    status: str
    created_at: datetime
    updated_at: datetime


class AdminProfileOut(BaseModel):
    """基础档案投影（敏感详情：需要 user 访问会话；no-store）。

    精确白名单：无 updated_at（User 模型不存在该列）、无 avatar_path、
    无 privacy_mode/created_by/deleted_at/credentials。
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    gender: str
    birth: dict[str, Any] | None = None
    death: dict[str, Any] | None = None
    bio: str | None = None
    avatar_available: bool
    profile_status: Literal["provisional", "identity_confirmed"]
    claim_status: Literal["managed", "claimed"]
    created_at: datetime


class AdminAttachmentMetadataOut(BaseModel):
    """附件安全元数据：永不返回 url_or_path/description/下载链接（无 size 列）。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    type: Literal["image", "link", "location"]
    title_safe: str | None = None
    created_at: datetime


# ---- 关系边与 confirmed 事实 ----


class AdminRelationOut(BaseModel):
    """结构化关系边投影；label 经脱敏（label-safe），原文证据永不查询。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    from_user_id: int
    from_user_name: str | None = None
    to_user_id: int
    to_user_name: str | None = None
    dir_class: Literal["elder", "younger", "peer", "spouse"]
    status: str
    space_id: int
    label_safe: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminFactOut(BaseModel):
    """confirmed 事实投影（state 恒 confirmed；provenance 为枚举，非原文）。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    fact_type: Literal[SOURCE_FACT_TYPES]  # type: ignore[valid-type]
    subject_user_id: int
    subject_name: str | None = None
    object_user_id: int
    object_name: str | None = None
    space_id: int | None = None
    state: Literal["confirmed"]
    provenance: str
    revision: int
    created_at: datetime
    updated_at: datetime


# ---- 运营队列 / 通知 ----


class AdminOperationsQueueItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["space_anomaly", "manager_application"]
    status: str
    reference_id: int
    space_id: int | None = None
    space_name: str | None = None
    space_kind: Literal["household", "lineage"] | None = None
    anomaly: str | None = None
    applicant_user_id: int | None = None
    applicant_name: str | None = None
    request_kind: str | None = None
    created_at: datetime


class AdminNotificationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    kind: str
    space_id: int
    recipient_account_id: int
    actor_user_id: int | None = None
    title: str
    summary: str | None = None
    created_at: datetime
    read_at: datetime | None = None


# ---- Agent 运行诊断 ----


class AdminAgentErrorOut(BaseModel):
    """二次脱敏诊断：无法可靠脱敏时 summary 为 None（只留 error_code + 位置）。"""

    model_config = ConfigDict(extra="forbid")

    error_code: str | None = None
    component: str | None = None
    stack_location: str | None = None
    summary: str | None = None


class AdminAgentRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    session_id: int
    job_id: int | None = None
    space_id: int
    account_id: int
    kind: str
    status: str
    attempt: int
    max_attempts: int
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool
    error_code: str | None = None
    error: AdminAgentErrorOut | None = None
    created_at: datetime
    updated_at: datetime
    settled_at: datetime | None = None


class AdminAgentJobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    run_id: int
    space_id: int | None = None
    account_id: int | None = None
    kind: str
    status: str
    attempt: int
    max_attempts: int
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool
    error: AdminAgentErrorOut | None = None
    created_at: datetime
    updated_at: datetime


# ---- 审计时间线 ----


class AdminAuditAccessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    system_admin_id: int | None = None
    session_id: int | None = None
    action: str
    target_type: Literal["user", "space"] | None = None
    target_id: int | None = None
    endpoint: str
    filters: dict[str, Any]
    result_count: int | None = None
    request_id: str | None = None
    ip: str | None = None
    created_at: datetime


# ---- 访问会话 ----


class AdminAccessSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["user", "space"]
    target_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_sane(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("理由不能为空")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in stripped):
            raise ValueError("理由不能包含控制字符")
        return stripped


class AdminAccessSessionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    target_type: Literal["user", "space"]
    target_id: int
    allowed_scopes: list[str]
    issued_at: datetime
    expires_at: datetime


# ---- 审批唯一写例外 ----


class AdminApplicationApproveRequest(BaseModel):
    """approve：理由可选；二次确认字段 confirm 必须显式为 true。"""

    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]
    note: str | None = Field(default=None, max_length=1000)


class AdminApplicationRejectRequest(BaseModel):
    """reject：理由必填非空；二次确认字段 confirm 必须显式为 true。"""

    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def _note_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("驳回理由不能为空白")
        return value


class AdminManagerApplicationOut(BaseModel):
    """申请裁决最小投影（申请人名 + 目标空间名，无家庭档案字段）。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    applicant_user_id: int
    applicant_name: str | None = None
    space_id: int
    space_name: str | None = None
    space_kind: Literal["household", "lineage"] | None = None
    request_kind: Literal["space_admin"]
    status: Literal["pending", "approved", "rejected"]
    decision_note: str | None = None
    transfer_consent_id: int | None = None
    transfer_consent_status: Literal["pending", "accepted", "rejected", "expired"] | None = None
    created_at: datetime
    decided_at: datetime | None = None
    system_admin_decided_by: int | None = None
