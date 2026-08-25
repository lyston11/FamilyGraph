"""关系域 Pydantic 模型（m1b）。字段与前端 types/api.ts 一一对应（人工同步）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DirClass = Literal["elder", "younger", "peer", "spouse"]
RelationStatus = Literal["pending", "active", "rejected", "cancelled", "revoked"]


class ConnectionRequestCreate(BaseModel):
    """合并请求创建体：relation 必填，space_membership 待 m1c 放开。"""

    model_config = ConfigDict(extra="forbid")

    target_id: int = Field(gt=0)
    dir_class: DirClass
    label: str | None = Field(default=None, max_length=64)
    # AD-4 合并语义的空间部分；m1c 落地前显式 422 SPACE_MEMBERSHIP_DEFERRED_M1C
    space_membership: dict[str, int] | None = None


class RelationViewOut(BaseModel):
    """viewer 视角的结构类与称谓（D3：label 恒为创建者视角原文）。"""

    dir_class: DirClass
    label: str | None
    label_from_creator: bool  # True=label 为创建者(=from_user)视角原文


class RelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_user: int
    to_user: int
    dir_class: DirClass
    label: str | None
    status: RelationStatus
    created_by: int
    view: RelationViewOut


class GraphNodeOut(BaseModel):
    id: int
    name: str
    gender: str


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[RelationOut]
    scope: Literal["family", "clan"]
