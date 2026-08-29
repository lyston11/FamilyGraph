"""图查询（m1b 骨架）：family scope ±depth / clan scope BFS 连通分量。

可见性过滤参数位预留（m2a 接入 visibility.py）；本任务按"本人关系图"语义
返回 active 边与节点。pending/rejected/cancelled/revoked 边不进图。
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import VALIDATION_ERROR, raise_api_error
from app.models.account import Account
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.schemas.relation import DirClass, GraphNodeOut, GraphOut, RelationOut, RelationViewOut
from app.services.kinship import display_relation

router = APIRouter(tags=["graph"])


def _collect_active_edges(
    session: Session, user_id: int, scope: str, depth: int
) -> tuple[set[int], list[Relation]]:
    """family：BFS ±depth；clan：全连通分量。返回 (节点集, 边集)。"""
    edges: list[Relation] = []
    nodes: set[int] = {user_id}
    frontier = {user_id}
    visited_edges: set[int] = set()
    current_depth = 0
    while frontier and (scope == "clan" or current_depth < depth):
        neighbors: set[int] = set()
        stmt = select(Relation).where(
            Relation.status == "active",
            or_(Relation.from_user.in_(frontier), Relation.to_user.in_(frontier)),
        )
        for edge in session.scalars(stmt):
            if edge.id not in visited_edges:
                visited_edges.add(edge.id)
                edges.append(edge)
                neighbors.add(edge.from_user)
                neighbors.add(edge.to_user)
        frontier = neighbors - nodes
        nodes |= neighbors
        current_depth += 1
    return nodes, edges


@router.get("/graph/me", response_model=GraphOut)
def my_graph(
    scope: str = Query(default="family", pattern="^(family|clan)$"),
    depth: int = Query(default=1, ge=1, le=10),
    space_id: int | None = Query(default=None, gt=0),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> GraphOut:
    actor, _account = identity
    if scope == "family" and depth < 1:  # pragma: no cover - Query 已约束
        raise_api_error(422, VALIDATION_ERROR, "depth 参数非法")

    node_ids, edges = _collect_active_edges(session, actor.id, scope, depth)

    # v2 可见性过滤：none 剔除；lineage_summary 节点裁剪为基线字段
    from app.services import visibility as vis

    levels: dict[int, str] = {}
    for eid in node_ids:
        if eid == actor.id:
            levels[eid] = vis.LEVEL_SELF_PRIVATE
            continue
        u = session.get(User, eid)
        decision = (
            vis.evaluate(session, actor, u, purpose=vis.PURPOSE_GRAPH) if u is not None else None
        )
        if decision is not None and decision.visible:
            levels[eid] = decision.level
        else:
            levels[eid] = vis.LEVEL_NONE
    # 只保留两端点均可见的边；隐藏端点的边整体丢弃（不泄露 ID/类型/标签/创建者视角）
    filtered_edges = [
        e
        for e in edges
        if levels.get(e.from_user, vis.LEVEL_NONE) != vis.LEVEL_NONE
        and levels.get(e.to_user, vis.LEVEL_NONE) != vis.LEVEL_NONE
    ]
    node_ids = {eid for eid, lv in levels.items() if lv != vis.LEVEL_NONE}

    # m1c：指定空间时，限定为该空间 active 成员的子图（家庭空间页数据源）
    if space_id is not None:
        space = session.get(FamilySpace, space_id)
        me = (
            session.query(SpaceMember)
            .filter(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == actor.id,
            )
            .first()
            if space is not None
            else None
        )
        if space is None or me is None or me.status != "active":
            raise_api_error(404, "SPACE_NOT_FOUND", "家庭空间不存在")
        member_rows = (
            session.query(SpaceMember)
            .filter(SpaceMember.space_id == space_id, SpaceMember.status == "active")
            .all()
        )
        allowed = {m.user_id for m in member_rows}
        node_ids &= allowed
        edges = [e for e in filtered_edges if e.from_user in allowed and e.to_user in allowed]
    else:
        edges = filtered_edges

    users = (
        session.query(User).filter(User.id.in_(node_ids)).order_by(User.id).all()
        if node_ids
        else []
    )
    nodes_out = []
    for u in users:
        level = levels.get(u.id, vis.LEVEL_SELF_PRIVATE)
        if level == vis.LEVEL_LINEAGE_SUMMARY:
            # lineage_summary 节点仅基线字段；性别不外泄
            nodes_out.append(
                GraphNodeOut(
                    id=u.id,
                    name=u.name,
                    gender="unknown",
                    visibility="lineage_summary",
                )
            )
        else:
            nodes_out.append(
                GraphNodeOut(
                    id=u.id,
                    name=u.name,
                    gender=u.gender,
                    visibility=(
                        "self_private" if level == vis.LEVEL_SELF_PRIVATE else "household_detail"
                    ),
                )
            )
    edges_out = []
    for edge in edges:
        dir_class, label, from_creator = display_relation(edge, actor.id)
        view = RelationViewOut(
            dir_class=cast(DirClass, dir_class),
            label=label,
            label_from_creator=from_creator,
        )
        edges_out.append(
            RelationOut(
                id=edge.id,
                from_user=edge.from_user,
                to_user=edge.to_user,
                dir_class=edge.dir_class,  # type: ignore[arg-type]
                label=edge.label,
                status=edge.status,  # type: ignore[arg-type]
                created_by=edge.created_by,
                view=view,
            )
        )
    return GraphOut(nodes=nodes_out, edges=edges_out, scope=cast(Literal["family", "clan"], scope))
