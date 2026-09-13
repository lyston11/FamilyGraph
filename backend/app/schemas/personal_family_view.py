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


class PersonalFamilyViewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    status: ViewStatus
    view_version: int
    computed_at: datetime | None
    nodes: list[PersonalFamilyViewNodeOut]
    edges: list[PersonalFamilyViewEdgeOut]
    topology_edges: list[PersonalFamilyViewTopologyEdgeOut] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: str | None = None
    stale_reason: str | None = None


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
