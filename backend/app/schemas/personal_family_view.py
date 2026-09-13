"""PersonalFamilyView and bridge API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ViewStatus = Literal["never_computed", "queued", "running", "current", "stale", "failed"]
BridgeStatus = Literal["pending", "active", "revoked", "expired", "rejected"]
VisibilityLevel = Literal["self_private", "household_detail", "lineage_summary"]
TopologyEdgeKind = Literal["parent", "spouse", "partner", "sibling"]
TopologyEdgeSubtype = Literal["biological", "adoptive", "step", "guardian"]


class PersonalFamilyViewNodeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    display: dict[str, Any]
    visibility_level: VisibilityLevel
    inclusion_reason_code: str


class PersonalFamilyViewEdgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_user_id: int
    to_user_id: int
    edge_kind: str
    path: list[dict[str, Any]]
    alternative_paths: list[list[dict[str, Any]]]
    path_class: str
    concept_code: str | None
    term: str | None
    inclusion_reason_code: str


class PersonalFamilyViewTopologyEdgeOut(BaseModel):
    """confirmed 直接亲属结构边：家族树世代/同辈布局的唯一依据。

    parent 一律 from=家长、to=子女并保留 subtype；spouse/partner/sibling
    对称无向（from 为较小 user_id，仅作规范化，不表达方向）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    from_user_id: int
    to_user_id: int
    edge_kind: TopologyEdgeKind
    subtype: TopologyEdgeSubtype | None = None


class InferredEdgeOut(BaseModel):
    """管家推测边（09-13 推测层；显示层投影，永不写 confirmed 事实）。

    - subject/object + relation_kind：单跳原子关系建议（虚线边渲染依据）；
    - term：单跳确定性称谓（subject→object 方向）；viewer_term/viewer_path：
      端点中「新上树成员」的 viewer 视角称谓与路径（无则 None/空）；
    - new_user_id：尚无 confirmed 路径的端点（两端均已知时为 None）；
    - id 为 steward_inferred_edges.id（确认/驳回操作端点寻址用）。
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    subject_user_id: int
    object_user_id: int
    relation_kind: str
    term: str | None
    path: list[dict[str, Any]]
    viewer_term: str | None
    viewer_path: list[dict[str, Any]]
    new_user_id: int | None
    evidence_fact_ids: list[int]
    revision: int
    created_at: datetime


class PFVProgress(BaseModel):
    """渐进读取进度块（09-13 progressive=true 显式启用；design §7.1 MVP 合同）。

    - phase：queued/preparing 前置、building 重算中、ready 本人结果齐全、
      retrying 输入漂移待重算、failed 终态；
    - completed_count/total_count：只统计当前查看者已授权目标；完成数来自
      已完整保存的 confirmed 摘要边，不是耗时百分比；
    - next_poll_ms：服务端建议的轮询间隔（ready/failed 为 0，停止高频轮询）；
    - generation/revision：viewer 内单调，客户端据此拒绝倒退响应。
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    phase: Literal["queued", "preparing", "building", "ready", "retrying", "failed"]
    generation: int
    revision: int
    completed_count: int
    total_count: int
    next_poll_ms: int


class PersonalFamilyViewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    status: ViewStatus
    view_version: int
    computed_at: datetime | None
    nodes: list[PersonalFamilyViewNodeOut]
    inferred_edges: list[InferredEdgeOut] = []
    edges: list[PersonalFamilyViewEdgeOut]
    topology_edges: list[PersonalFamilyViewTopologyEdgeOut] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: str | None = None
    stale_reason: str | None = None
    progress: PFVProgress | None = None


class PFVDemandIn(BaseModel):
    """渐进按需重算登记（09-13 design §7.1）：认证身份即 viewer，不接受任意视角。

    - space_id 必须是本人 active 成员空间；
    - focus_user_id 可选：必须属于当前授权骨架（可见集合），只用于服务端
      记录重点关注目标（提升下次重建的处理顺序参考），绝不扩大授权范围。
    """

    model_config = ConfigDict(extra="forbid")

    space_id: int = Field(gt=0)
    focus_user_id: int | None = Field(default=None, gt=0)


class PFVDemandOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["queued", "already_active"]
    focus_user_id: int | None = None


class PersonalFamilyBridgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    other_space_id: int = Field(gt=0)
    other_anchor_user_id: int = Field(gt=0)
    anchor_user_id: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None
    scope: dict[str, Any] = Field(default_factory=lambda: {"mode": "anchor_paths"})


class PersonalFamilyBridgeConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)


class PersonalFamilyBridgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    lineage_space_a_id: int
    lineage_space_b_id: int
    anchor_a_user_id: int
    anchor_b_user_id: int
    status: BridgeStatus
    revision: int
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "BridgeStatus",
    "PFVDemandIn",
    "PFVDemandOut",
    "PFVProgress",
    "PersonalFamilyBridgeConsent",
    "PersonalFamilyBridgeCreate",
    "PersonalFamilyBridgeOut",
    "PersonalFamilyViewEdgeOut",
    "PersonalFamilyViewNodeOut",
    "PersonalFamilyViewOut",
    "PersonalFamilyViewTopologyEdgeOut",
    "TopologyEdgeKind",
    "TopologyEdgeSubtype",
    "ViewStatus",
    "VisibilityLevel",
]
