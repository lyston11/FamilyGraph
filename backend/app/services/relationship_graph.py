"""Scoped relationship graph builder（V2.3 Block E2，KI-2）。

从 confirmed SourceFact 构建当前 (viewer, space) 口径下的关系图快照：

- 事实口径：state=confirmed 且 (space_id 匹配 OR 全局 NULL)；social_relations
  根本不入图（design.md：社会边不参加血缘/姻亲路径）。
- 节点口径：viewer 在该空间的可见人集合——active 成员 ∪ active 最小引用 ∪
  本人，逐一过 visibility.evaluate(space_context, purpose=agent) 剪枝，
  与 V2.2 agent_query._space_candidate_ids 同一口径；不可见节点连边一并剪除
  （防存在性泄露）。
- 边词汇：parent 四型（subject 是 object 的家长，子方向查询反向遍历）、
  spouse/partner（对称，subtype 区分）、direct_sibling（对称，父母未知时独立
  成立，不反推父母）。
- 快照指纹 snapshot_hash：参与计算的 (fact_id, revision, fact_type) 按 fact_id
  升序逐行 "id:revision:type" 拼接后 SHA256 —— DerivedFact 缓存正确性依据。

纯函数纪律：相同 facts 快照 + algorithm_version 必须产出相同结果（AC-KI7）；
本模块不含任何 LLM 参与。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember, SpaceProfileRef
from app.models.user import User
from app.services import visibility
from app.services.source_facts import FACT_CONFIRMED

# ---- 边类型与确定性排序 ----

# 规范化边类型（step.edge_type 合同值）
EDGE_PARENT = "parent"
EDGE_SPOUSE = "spouse"
EDGE_PARTNER = "partner"
EDGE_SIBLING = "sibling"

# parent 类 fact_type → step.subtype（biological/adoptive/step/guardian）
_SUBTYPE_BY_FACT_TYPE = {
    "biological_parent": "biological",
    "adoptive_parent": "adoptive",
    "step_parent": "step",
    "guardian": "guardian",
}

# 遍历与主路径排序的确定性依据：邻接表按键排序，同键按 (to_id, edge_type, fact_id)
_EDGE_ORDER = {EDGE_PARENT: 0, EDGE_SIBLING: 1, EDGE_SPOUSE: 2, EDGE_PARTNER: 3}


@dataclass(frozen=True)
class GraphEdge:
    """规范化图边：from 节点出发指向 to_id。

    - parent：direction="up" 表示 to 是 from 的家长；"down" 反向；
      subtype 取自 fact_type（biological/adoptive/step/guardian）。
    - spouse/partner/sibling：direction="sym"，对称边双向各存一条。
    """

    to_id: int
    edge_type: str
    subtype: str | None
    direction: str
    fact_id: int


@dataclass(frozen=True)
class RelationshipGraph:
    """(viewer, space) 口径的图快照：节点性别 + 邻接表 + 指纹。"""

    viewer_user_id: int
    space_id: int
    node_genders: dict[int, str]
    adjacency: dict[int, list[GraphEdge]]
    snapshot_hash: str


def _visible_node_ids(session: Session, *, viewer_user_id: int, space_id: int) -> set[int]:
    """viewer 在 space 内可见人集合（active 成员 ∪ active 引用 ∪ 本人）。

    与 agent_query._space_candidate_ids 同口径；每个候选再经
    visibility.evaluate(purpose=agent) 单点判定，none 层级剔除。
    """
    member_ids = set(
        session.scalars(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == space_id, SpaceMember.status == "active"
            )
        ).all()
    )
    ref_ids = set(
        session.scalars(
            select(SpaceProfileRef.user_id).where(
                SpaceProfileRef.space_id == space_id, SpaceProfileRef.status == "active"
            )
        ).all()
    )
    candidates = member_ids | ref_ids | {viewer_user_id}
    viewer = session.get(User, viewer_user_id)
    if viewer is None:
        return set()
    visible: set[int] = set()
    for uid in sorted(candidates):
        target = session.get(User, uid)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, viewer, target, space_context=space_id, purpose=visibility.PURPOSE_AGENT
        )
        if decision.visible:
            visible.add(uid)
    return visible


def load_graph(session: Session, *, viewer_user_id: int, space_id: int) -> RelationshipGraph:
    """构建并返回当前空间口径的关系图快照（含 snapshot_hash 指纹）。"""
    visible = _visible_node_ids(session, viewer_user_id=viewer_user_id, space_id=space_id)

    stmt = select(SourceFact).where(
        SourceFact.state == FACT_CONFIRMED,
        or_(SourceFact.space_id == space_id, SourceFact.space_id.is_(None)),
    )
    participating: list[SourceFact] = []
    for row in session.scalars(stmt):
        if row.subject_user_id in visible and row.object_user_id in visible:
            participating.append(row)
    participating.sort(key=lambda fact: fact.id)  # 稳定指纹与遍历序的基础

    genders: dict[int, str] = {}
    for uid in sorted(visible):
        user = session.get(User, uid)
        if user is not None:
            genders[uid] = user.gender

    adjacency: dict[int, list[GraphEdge]] = {uid: [] for uid in genders}
    for fact in participating:
        subject_id, object_id = fact.subject_user_id, fact.object_user_id
        if fact.fact_type in _SUBTYPE_BY_FACT_TYPE:
            subtype = _SUBTYPE_BY_FACT_TYPE[fact.fact_type]
            # 方向合同：subject 是 object 的家长 → object 出发为 up
            adjacency.setdefault(object_id, []).append(
                GraphEdge(subject_id, EDGE_PARENT, subtype, "up", fact.id)
            )
            adjacency.setdefault(subject_id, []).append(
                GraphEdge(object_id, EDGE_PARENT, subtype, "down", fact.id)
            )
        elif fact.fact_type in ("spouse", "partner"):
            edge_type = EDGE_SPOUSE if fact.fact_type == "spouse" else EDGE_PARTNER
            adjacency.setdefault(subject_id, []).append(
                GraphEdge(object_id, edge_type, None, "sym", fact.id)
            )
            adjacency.setdefault(object_id, []).append(
                GraphEdge(subject_id, edge_type, None, "sym", fact.id)
            )
        elif fact.fact_type == "direct_sibling":
            adjacency.setdefault(subject_id, []).append(
                GraphEdge(object_id, EDGE_SIBLING, None, "sym", fact.id)
            )
            adjacency.setdefault(object_id, []).append(
                GraphEdge(subject_id, EDGE_SIBLING, None, "sym", fact.id)
            )

    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge.to_id, _EDGE_ORDER[edge.edge_type], edge.fact_id))

    digest = hashlib.sha256()
    for fact in participating:
        digest.update(f"{fact.id}:{fact.revision}:{fact.fact_type}\n".encode())
    return RelationshipGraph(
        viewer_user_id=viewer_user_id,
        space_id=space_id,
        node_genders=genders,
        adjacency=adjacency,
        snapshot_hash=digest.hexdigest(),
    )
