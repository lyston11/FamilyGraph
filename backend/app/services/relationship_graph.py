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
- 桥接口径：仅承认 status=active 且 expires_at 为空或严格晚于当前 UTC 时间的
  bridge（到期边界等于当前时刻即失效；读取时过滤，不等后台写 expired 状态），
  节点可见性、路径、拓扑授权与快照指纹使用同一有效桥接集合。
- 快照指纹 snapshot_hash：参与计算的 (fact_id, revision, fact_type) 按 fact_id
  升序逐行 "id:revision:type" 拼接后 SHA256 —— DerivedFact 缓存正确性依据。

纯函数纪律：相同 facts 快照 + algorithm_version 必须产出相同结果（AC-KI7）；
本模块不含任何 LLM 参与。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.personal_family_view import PersonalFamilyBridge
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember, SpaceProfileRef
from app.models.user import User
from app.services import visibility
from app.services.source_facts import FACT_CONFIRMED
from app.utils.timeutil import utcnow

# ---- 边类型与确定性排序 ----

# 规范化边类型（step.edge_type 合同值）
EDGE_PARENT = "parent"
EDGE_SPOUSE = "spouse"
EDGE_PARTNER = "partner"
EDGE_SIBLING = "sibling"
EDGE_BRIDGE = "bridge"

# parent 类 fact_type → step.subtype（biological/adoptive/step/guardian）
_SUBTYPE_BY_FACT_TYPE = {
    "biological_parent": "biological",
    "adoptive_parent": "adoptive",
    "step_parent": "step",
    "guardian": "guardian",
}

# 对称 fact_type → 结构边 kind（无方向；direct_sibling 表达父母未知的兄弟姐妹）
_SYMMETRIC_KIND_BY_FACT_TYPE = {
    "spouse": EDGE_SPOUSE,
    "partner": EDGE_PARTNER,
    "direct_sibling": EDGE_SIBLING,
}

# 遍历与主路径排序的确定性依据：邻接表按键排序，同键按 (to_id, edge_type, fact_id)
_EDGE_ORDER = {EDGE_PARENT: 0, EDGE_SIBLING: 1, EDGE_SPOUSE: 2, EDGE_PARTNER: 3, EDGE_BRIDGE: 4}


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
    bridge_user_ids: frozenset[int] = frozenset()


def birth_from_user(user: User) -> tuple[str, int] | None:
    """解析结构化出生日期为 (cal_type, 年份)；无法解析返回 None。

    cal_type 仅接受 visibility.is_minor 同款 solar/lunar；年份数字在日历内
    单调，可用于长幼比较（混合日历的比较由消费方拒绝）。
    仅供展示层长幼消歧使用（脱敏判定由调用方的可见性决定承载），不下发。
    """
    birth = user.birth if isinstance(user.birth, dict) else {}
    date_str = birth.get("date") if birth.get("cal_type") in ("solar", "lunar") else None
    if not date_str or len(date_str) < 4:
        return None
    try:
        return (str(birth["cal_type"]), int(str(date_str)[:4]))
    except ValueError:
        return None


def load_birth_years(
    session: Session, *, viewer_user_id: int, space_id: int, user_ids: set[int] | list[int]
) -> dict[int, tuple[str, int] | None]:
    """按 (viewer, 各目标) 的展示口径（PURPOSE_GRAPH）可见性决定收集出生数据。

    与 PFV 展示同一语义：birth 字段对 viewer 非 FIELD_CLEAR（含未成年人
    overlay / lineage 脱敏）或不可解析 → None。组合层称谓长幼消歧消费。
    """
    viewer = session.get(User, viewer_user_id)
    if viewer is None:
        return {}
    result: dict[int, tuple[str, int] | None] = {}
    for uid in sorted({int(u) for u in user_ids}):
        target = session.get(User, uid)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, viewer, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
        )
        if not decision.visible or decision.fields.get("birth") != visibility.FIELD_CLEAR:
            result[uid] = None
            continue
        result[uid] = birth_from_user(target)
    return result


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


def scoped_confirmed_facts(
    session: Session,
    *,
    space_ids: set[int],
    visible_ids: set[int] | None = None,
) -> list[SourceFact]:
    """当前空间集合（含全局 NULL 事实）内的 confirmed 事实，按 fact.id 稳定排序。

    load_graph 传自己的授权空间集合（含 bridge 对方空间）；拓扑等消费方传更窄
    的空间范围，并可用 visible_ids 要求两端点均可见（防存在性泄露）。
    """
    stmt = select(SourceFact).where(
        SourceFact.state == FACT_CONFIRMED,
        or_(SourceFact.space_id.is_(None), SourceFact.space_id.in_(space_ids)),
    )
    rows = session.scalars(stmt).all()
    if visible_ids is not None:
        rows = [
            row
            for row in rows
            if row.subject_user_id in visible_ids and row.object_user_id in visible_ids
        ]
    return sorted(rows, key=lambda fact: fact.id)


def topology_edges_from_facts(facts: Iterable[SourceFact]) -> list[dict[str, Any]]:
    """将 confirmed 原子事实规范化为去重、稳定排序的直接亲属结构边。

    - parent 类：from=家长(subject)、to=子女(object)，subtype 保留事实原义；
    - spouse/partner/direct_sibling：对称无向，较小 user_id 为 from；
    - 按 (edge_kind, subtype, from, to) 去重：全局/空间重复事实与反向申报的
      对称事实合并，不同 parent subtype 不合并；id 由规范化四元组生成，
      不依赖 fact.id、viewer 或遍历顺序；
    - 自环与未知 fact_type 丢弃；bridge 与推测数据不在 SourceFact 输入内。
    """
    edges: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    for fact in facts:
        subject_id, object_id = fact.subject_user_id, fact.object_user_id
        if subject_id == object_id:
            continue
        if fact.fact_type in _SUBTYPE_BY_FACT_TYPE:
            kind = EDGE_PARENT
            subtype: str | None = _SUBTYPE_BY_FACT_TYPE[fact.fact_type]
            from_id, to_id = subject_id, object_id
        elif fact.fact_type in _SYMMETRIC_KIND_BY_FACT_TYPE:
            kind = _SYMMETRIC_KIND_BY_FACT_TYPE[fact.fact_type]
            subtype = None
            from_id, to_id = sorted((subject_id, object_id))
        else:
            continue
        key = (kind, subtype or "", from_id, to_id)
        if key not in edges:
            edges[key] = {
                "id": f"{kind}:{subtype or '-'}:{from_id}:{to_id}",
                "from_user_id": from_id,
                "to_user_id": to_id,
                "edge_kind": kind,
                "subtype": subtype,
            }
    return [edges[key] for key in sorted(edges)]


def load_graph(session: Session, *, viewer_user_id: int, space_id: int) -> RelationshipGraph:
    """构建并返回当前空间口径的关系图快照（含 snapshot_hash 指纹）。"""
    visible = _visible_node_ids(session, viewer_user_id=viewer_user_id, space_id=space_id)
    bridge_user_ids: set[int] = set()
    bridge_space_ids: set[int] = set()
    # 有效桥接 = active 且未到期（expires_at 为空或严格晚于当前 UTC 时刻）；
    # 仅时钟跨过 expires_at 也立即失去授权，节点/路径/拓扑/指纹同一集合。
    active_bridges = session.scalars(
        select(PersonalFamilyBridge).where(
            PersonalFamilyBridge.status == "active",
            or_(
                PersonalFamilyBridge.expires_at.is_(None),
                PersonalFamilyBridge.expires_at > utcnow(),
            ),
            or_(
                (PersonalFamilyBridge.lineage_space_a_id == space_id)
                & (PersonalFamilyBridge.anchor_a_user_id == viewer_user_id),
                (PersonalFamilyBridge.lineage_space_b_id == space_id)
                & (PersonalFamilyBridge.anchor_b_user_id == viewer_user_id),
            ),
        )
    ).all()
    for bridge in active_bridges:
        if bridge.lineage_space_a_id == space_id:
            other_space_id = bridge.lineage_space_b_id
            other_anchor_id = bridge.anchor_b_user_id
        else:
            other_space_id = bridge.lineage_space_a_id
            other_anchor_id = bridge.anchor_a_user_id
        bridge_space_ids.add(other_space_id)
        bridge_user_ids.add(other_anchor_id)
        bridge_member_ids = session.scalars(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == other_space_id, SpaceMember.status == "active"
            )
        ).all()
        bridge_user_ids.update(bridge_member_ids)
    visible.update(bridge_user_ids)

    authorized_space_ids = {space_id, *bridge_space_ids}
    participating = scoped_confirmed_facts(
        session, space_ids=authorized_space_ids, visible_ids=visible
    )

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

    for bridge in active_bridges:
        other_anchor_id = (
            bridge.anchor_b_user_id
            if bridge.lineage_space_a_id == space_id
            else bridge.anchor_a_user_id
        )
        adjacency.setdefault(viewer_user_id, []).append(
            GraphEdge(other_anchor_id, EDGE_BRIDGE, None, "sym", 0)
        )

    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge.to_id, _EDGE_ORDER[edge.edge_type], edge.fact_id))

    digest = hashlib.sha256()
    for fact in participating:
        digest.update(f"{fact.id}:{fact.revision}:{fact.fact_type}\n".encode())
    for bridge in active_bridges:
        digest.update(
            f"bridge:{bridge.id}:{bridge.revision}:{bridge.lineage_space_a_id}:{bridge.lineage_space_b_id}\n".encode()
        )
    return RelationshipGraph(
        viewer_user_id=viewer_user_id,
        space_id=space_id,
        node_genders=genders,
        adjacency=adjacency,
        snapshot_hash=digest.hexdigest(),
        bridge_user_ids=frozenset(bridge_user_ids),
    )
