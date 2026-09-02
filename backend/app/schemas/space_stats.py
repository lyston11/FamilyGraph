"""Space stats API contract（字段白名单，与前端 spaceStats.ts decoder 对齐）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.personal_family_view import ViewStatus

DirClass = Literal["elder", "younger", "peer", "spouse"]


class SpaceStatsRelationSliceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir_class: DirClass
    count: int


class SpaceStatsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    space_kind: Literal["household", "lineage"]
    status: ViewStatus
    view_version: int | None
    node_count: int
    edge_count: int
    member_count: int
    relation_distribution: list[SpaceStatsRelationSliceOut]
    pending_action_cards: int
    pending_memberships: int
    computed_at: datetime | None
    stale_reason: str | None


__all__ = ["DirClass", "SpaceStatsOut", "SpaceStatsRelationSliceOut"]
