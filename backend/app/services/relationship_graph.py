"""Scoped relationship graph builder（V2.3 Block E2，KI-2）。

从 confirmed SourceFact 构建当前 (viewer, space) 口径下的关系图快照：

- 事实口径：state=confirmed 且 (space_id 匹配 OR 全局 NULL)；social_relations
  根本不入图（design.md：社会边不参加血缘/姻亲路径）。
- 节点口径：viewer 在该空间的可见人集合——active 成员 ∪ active 最小引用 ∪
  本人，逐一过 visibility.evaluate(space_context, purpose=agent) 剪枝，
  与 V2.2 agent_query._space_candidate_ids 同一口径；不可见节点连边一并剪除
  （防存在性泄露）。
- 路径口径（09-19）：事实至少一端接入节点集合，且两端点都对 viewer 可见
  （purpose=graph）。中间人可以不是本空间成员（例如共享父母）——不如此则
  亲属路径会被切断；但中间人**不进入节点集合**，只进入路径证据可见集
  （``path_genders`` / ``path_user_ids``）。
- 边词汇：parent 四型（subject 是 object 的家长，子方向查询反向遍历）、
  spouse/partner（对称，subtype 区分）、direct_sibling（对称，父母未知时独立
  成立，不反推父母）。
- 桥接口径：仅承认 status=active 且 expires_at 为空或严格晚于当前 UTC 时间的
  bridge（到期边界等于当前时刻即失效；读取时过滤，不等后台写 expired 状态），
  节点可见性、路径、拓扑授权与快照指纹使用同一有效桥接集合。
- 快照指纹 snapshot_hash：viewer/space、授权节点与性别、实际事实内容、
  有效桥接/成员和确认邻接表的版本化 SHA256；展示字段和推测边独立失效。

纯函数纪律：相同 facts 快照 + algorithm_version 必须产出相同结果（AC-KI7）；
本模块不含任何 LLM 参与。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, TypeVar

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

GRAPH_SNAPSHOT_VERSION = "authorized-graph-v3"

_K = TypeVar("_K")
_V = TypeVar("_V")


class FrozenMapping(Mapping[_K, _V]):
    """A detached, read-only mapping with an explicit pickle representation.

    MappingProxyType alone cannot cross a spawn boundary. Copy before wrapping,
    so neither the caller's original dict nor the receiver can mutate a snapshot.
    Values must themselves be immutable (graph edges/terms use frozen DTOs).
    """

    __slots__ = ("_data",)
    _data: Mapping[_K, _V]

    def __init__(self, values: Mapping[_K, _V]) -> None:
        object.__setattr__(self, "_data", MappingProxyType(dict(values)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("snapshot mappings are immutable")

    def __getitem__(self, key: _K) -> _V:
        return self._data[key]

    def __iter__(self) -> Iterator[_K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return type(self), (dict(self._data),)


@dataclass(frozen=True)
class GraphEdge:
    """规范化图边：from 节点出发指向 to_id。

    - parent：direction="up" 表示 to 是 from 的家长；"down" 反向；
      subtype 取自 fact_type（biological/adoptive/step/guardian）。
    - spouse/partner/sibling：direction="sym"，对称边双向各存一条。
    - 推测边（extra_edges 注入）：fact_id = -(推测边 id) < 0，与 confirmed
      事实（fact_id > 0）、bridge（fact_id = 0）区分；消费方据负值识别推测步。
    """

    to_id: int
    edge_type: str
    subtype: str | None
    direction: str
    fact_id: int


@dataclass(frozen=True)
class ExtraEdge:
    """增广图注入的推测单跳（PFV 推测层消费；id 为 steward_inferred_edges.id）。

    subject 是 object 的家长类关系（parent 四型）或对称关系（spouse/partner/
    direct_sibling）。fact_id 恒为 -id（负值标记推测步）。
    """

    edge_id: int
    subject_user_id: int
    object_user_id: int
    relation_kind: str

    @property
    def fact_id(self) -> int:
        return -self.edge_id


_INFERENCE_PARENT_SUBTYPES = {
    "biological_parent": "biological",
    "adoptive_parent": "adoptive",
    "step_parent": "step",
    "guardian": "guardian",
}


@dataclass(frozen=True)
class GraphFact:
    """Confirmed evidence copied from SourceFact, without ORM state."""

    id: int
    revision: int
    fact_type: str
    state: str
    space_id: int | None
    subject_user_id: int
    object_user_id: int


@dataclass(frozen=True)
class GraphBridge:
    """Effective bridge scope and membership at the graph's read snapshot."""

    id: int
    revision: int
    lineage_space_a_id: int
    lineage_space_b_id: int
    anchor_a_user_id: int
    anchor_b_user_id: int
    expires_at: datetime | None
    member_user_ids: tuple[int, ...]
    scope_json: str
    consent_a_account_id: int | None
    consent_b_account_id: int | None


@dataclass(frozen=True)
class RelationshipGraph:
    """Detached (viewer, space) snapshot, safe to send to a spawn worker.

    Confirmed evidence/scope remains available for authorization checks and
    topology preparation. Extra inferred edges affect only adjacency, never the
    confirmed snapshot hash or confirmed evidence collection.
    """

    viewer_user_id: int
    space_id: int
    # 节点集合：仅当前空间的授权候选（active 成员 ∪ active 引用 ∪ 本人，经
    # PURPOSE_GRAPH 重验）。消费方据此决定「谁出现在家族树/谁是称谓目标」。
    node_genders: Mapping[int, str]
    # 邻接表覆盖路径可见集内的全部节点：路径枚举需要穿过不是本空间成员的
    # 中间人（例如共享父母），否则亲属称谓会被切断。
    adjacency: Mapping[int, Sequence[GraphEdge]]
    snapshot_hash: str
    # 路径证据可见集（含中间人）的性别表。**不是**节点集合：不得用它决定节点
    # 是否进入家族树、也不得据此扩大任何字段投影；它只用于路径编码/描述与
    # 路径证据的逐条重验。恒为 node_genders 的超集。
    path_genders: Mapping[int, str] = field(default_factory=dict)
    bridge_user_ids: frozenset[int] = frozenset()
    confirmed_facts: tuple[GraphFact, ...] = ()
    bridges: tuple[GraphBridge, ...] = ()
    authorized_space_ids: frozenset[int] = frozenset()
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_genders", FrozenMapping(self.node_genders))
        object.__setattr__(self, "path_genders", FrozenMapping(self.path_genders))
        object.__setattr__(
            self,
            "adjacency",
            FrozenMapping({uid: tuple(edges) for uid, edges in self.adjacency.items()}),
        )

    @property
    def path_user_ids(self) -> frozenset[int]:
        """路径证据可见集的 id 集合（含中间人），供逐条重验使用。"""
        return frozenset(self.path_genders)


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


def _path_visible_user_ids(
    session: Session,
    *,
    viewer_user_id: int,
    space_id: int,
    facts: Iterable[SourceFact],
) -> set[int]:
    """路径证据可见集：本批事实端点中对 viewer 可见的人（含中间人）。

    口径为 ``PURPOSE_GRAPH``，与 PFV/Steward 展示同源。只对本批事实的端点
    逐个求值——不扫全库（``visibility.visible_user_ids`` 是全库口径，既越
    过「跨 lineage 连接必须走显式 bridge」的边界，也要付全量成本）。

    返回集只用于路径枚举与逐条重验；**不**决定节点集合，也不扩大字段投影。
    """
    viewer = session.get(User, viewer_user_id)
    if viewer is None:
        return set()
    endpoints: set[int] = set()
    for fact in facts:
        endpoints.add(fact.subject_user_id)
        endpoints.add(fact.object_user_id)
    visible: set[int] = set()
    for uid in sorted(endpoints):
        target = session.get(User, uid)
        if target is None:
            continue
        decision = visibility.evaluate(
            session, viewer, target, space_context=space_id, purpose=visibility.PURPOSE_GRAPH
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


def viewer_reachable_user_ids(session: Session, *, viewer_user_id: int) -> set[int]:
    """viewer 在自己的授权口径下可经 confirmed 亲属链到达的人（含本人）。

    准入门禁用（join_request）：只问「我与对方家族是否有已确认的亲属联系」，
    因此不能用目标空间的图口径——那个口径按设计只暴露空间内成员，会把申请人
    自己一侧的中间人连同目标成员一起隐去，导致门禁永不通过。

    口径：
    - 事实范围 = 申请人 active 空间（含全局 NULL）内的 confirmed 事实；
    - 两端点都必须对申请人可见（``space_context=None``，与既有资料可见性
      门禁同一口径）——不返回申请人本来就看不到的人；
    - 亲属链按无向连通处理：准入只问「有无亲属联系」，方向不影响结论。

    只读、不写状态、不扩大任何字段投影。
    """
    viewer = session.get(User, viewer_user_id)
    if viewer is None:
        return set()
    space_ids = set(
        session.scalars(
            select(SpaceMember.space_id).where(
                SpaceMember.user_id == viewer_user_id, SpaceMember.status == "active"
            )
        ).all()
    )
    facts = scoped_confirmed_facts(session, space_ids=space_ids or {0})
    endpoint_visible: dict[int, bool] = {viewer_user_id: True}

    def _visible(uid: int) -> bool:
        cached = endpoint_visible.get(uid)
        if cached is not None:
            return cached
        target = session.get(User, uid)
        ok = (
            target is not None
            and visibility.evaluate(
                session, viewer, target, purpose=visibility.PURPOSE_PROFILE
            ).visible
        )
        endpoint_visible[uid] = ok
        return ok

    adjacency: dict[int, set[int]] = {}
    for fact in facts:
        subject_id, object_id = fact.subject_user_id, fact.object_user_id
        if subject_id == object_id or not _visible(subject_id) or not _visible(object_id):
            continue
        adjacency.setdefault(subject_id, set()).add(object_id)
        adjacency.setdefault(object_id, set()).add(subject_id)
    reached = {viewer_user_id}
    stack = [viewer_user_id]
    while stack:
        for nxt in adjacency.get(stack.pop(), ()):
            if nxt not in reached:
                reached.add(nxt)
                stack.append(nxt)
    return reached


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


def load_graph(
    session: Session,
    *,
    viewer_user_id: int,
    space_id: int,
    extra_edges: list[ExtraEdge] | tuple[ExtraEdge, ...] | None = None,
) -> RelationshipGraph:
    """构建并返回当前空间口径的关系图快照（含 snapshot_hash 指纹）。

    ``extra_edges``（推测层，缺省 None = 行为与推测层上线前逐字节一致）：把
    proposed 推测单跳注入邻接表参与路径枚举；与 confirmed 事实同结构的推测边
    跳过（不重复）。snapshot_hash 恒为 confirmed 事实指纹——推测层不进入缓存
    语义，DerivedFact 缓存（恒不传 extra_edges）不受影响。
    """
    visible = _visible_node_ids(session, viewer_user_id=viewer_user_id, space_id=space_id)
    bridge_user_ids: set[int] = set()
    bridge_space_ids: set[int] = set()
    # 有效桥接 = active 且未到期（expires_at 为空或严格晚于当前 UTC 时刻）；
    # 仅时钟跨过 expires_at 也立即失去授权，节点/路径/拓扑/指纹同一集合。
    active_bridges = session.scalars(
        select(PersonalFamilyBridge)
        .where(
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
        .order_by(PersonalFamilyBridge.id)
    ).all()
    bridge_snapshots: list[GraphBridge] = []
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
        bridge_snapshots.append(
            GraphBridge(
                id=bridge.id,
                revision=bridge.revision,
                lineage_space_a_id=bridge.lineage_space_a_id,
                lineage_space_b_id=bridge.lineage_space_b_id,
                anchor_a_user_id=bridge.anchor_a_user_id,
                anchor_b_user_id=bridge.anchor_b_user_id,
                expires_at=bridge.expires_at,
                member_user_ids=tuple(sorted(bridge_member_ids)),
                scope_json=json.dumps(bridge.scope_json, sort_keys=True, separators=(",", ":")),
                consent_a_account_id=bridge.consent_a_account_id,
                consent_b_account_id=bridge.consent_b_account_id,
            )
        )
    visible.update(bridge_user_ids)

    authorized_space_ids = {space_id, *bridge_space_ids}
    # 事实口径（09-19 D3）：不再要求「两端点都在节点集合内」——那会把共享父母
    # 这类不是本空间成员的中间人整条边剔除，从而切断亲属路径（朱元璋在李氏家族
    # 看不到姐姐/姐夫）。新口径：
    #   1. 至少一端接入本空间（节点集合），保证每条入图事实真的连到本空间的人，
    #      不把与本空间无关的事实子图拉进邻接表；
    #   2. 两端点都对 viewer 可见（PURPOSE_GRAPH），保证不泄露不可见人物。
    # 中间人因此可以出现在邻接表里参与路径枚举，但绝不进入 node_genders。
    connected = [
        fact
        for fact in scoped_confirmed_facts(session, space_ids=authorized_space_ids)
        if fact.subject_user_id in visible or fact.object_user_id in visible
    ]
    path_visible = _path_visible_user_ids(
        session, viewer_user_id=viewer_user_id, space_id=space_id, facts=connected
    )
    # 显式 bridge 授权是独立的跨空间授权（spec §11）：另一侧 anchor 与其空间
    # active 成员按最小 lineage_summary 参与路径计算，与 _visible_node_ids 的
    # 既有 union 语义一致（不重走 evaluate，否则 bridge 路径会整体失效）。
    path_visible |= bridge_user_ids
    participating = [
        fact
        for fact in connected
        if fact.subject_user_id in path_visible and fact.object_user_id in path_visible
    ]

    genders: dict[int, str] = {}
    path_genders: dict[int, str] = {}
    for uid in sorted(visible):
        user = session.get(User, uid)
        if user is not None:
            genders[uid] = user.gender
            path_genders[uid] = user.gender
    # 路径可见集还包含参与路径的中间人（不在本空间候选内）：邻接表会引用它们，
    # 路径编码/描述与逐条重验都需要它们的性别。
    for uid in sorted(path_visible):
        if uid in path_genders:
            continue
        user = session.get(User, uid)
        if user is not None:
            path_genders[uid] = user.gender

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

    # Hash the actual authorized confirmed input before adding optional inferred
    # edges. Revisions alone do not cover gender corrections, changed endpoints,
    # visibility changes or bridges expiring without an event.
    confirmed_facts = tuple(
        GraphFact(
            id=fact.id,
            revision=fact.revision,
            fact_type=fact.fact_type,
            state=fact.state,
            space_id=fact.space_id,
            subject_user_id=fact.subject_user_id,
            object_user_id=fact.object_user_id,
        )
        for fact in participating
    )
    hash_payload = {
        "version": GRAPH_SNAPSHOT_VERSION,
        "viewer": viewer_user_id,
        "space": space_id,
        "authorized_spaces": sorted(authorized_space_ids),
        "nodes": sorted(genders.items()),
        "facts": [
            (
                f.id,
                f.revision,
                f.fact_type,
                f.state,
                f.space_id,
                f.subject_user_id,
                f.object_user_id,
            )
            for f in confirmed_facts
        ],
        "bridges": [
            (
                b.id,
                b.revision,
                b.lineage_space_a_id,
                b.lineage_space_b_id,
                b.anchor_a_user_id,
                b.anchor_b_user_id,
                b.expires_at.isoformat() if b.expires_at else None,
                b.member_user_ids,
                b.scope_json,
                b.consent_a_account_id,
                b.consent_b_account_id,
            )
            for b in bridge_snapshots
        ],
        "adjacency": [
            (
                uid,
                [
                    (edge.to_id, edge.edge_type, edge.subtype, edge.direction, edge.fact_id)
                    for edge in sorted(
                        edges,
                        key=lambda edge: (edge.to_id, _EDGE_ORDER[edge.edge_type], edge.fact_id),
                    )
                ],
            )
            for uid, edges in sorted(adjacency.items())
        ],
    }
    snapshot_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    if extra_edges:
        confirmed_keys: set[tuple[int, int, str]] = set()
        for fact in participating:
            ftype = fact.fact_type
            pair = (
                {fact.subject_user_id, fact.object_user_id}
                if ftype
                in (
                    "spouse",
                    "partner",
                    "direct_sibling",
                )
                else None
            )
            if pair is not None:
                confirmed_keys |= {
                    (fact.subject_user_id, fact.object_user_id, ftype),
                    (fact.object_user_id, fact.subject_user_id, ftype),
                }
            else:
                confirmed_keys.add((fact.subject_user_id, fact.object_user_id, ftype))
        for extra in sorted(extra_edges, key=lambda item: item.edge_id):
            sym = extra.relation_kind in ("spouse", "partner", "direct_sibling")
            supported = sym or extra.relation_kind in _INFERENCE_PARENT_SUBTYPES
            if not supported:
                continue
            if extra.subject_user_id not in visible or extra.object_user_id not in visible:
                continue
            keys = (
                {
                    (extra.subject_user_id, extra.object_user_id, extra.relation_kind),
                    (extra.object_user_id, extra.subject_user_id, extra.relation_kind),
                }
                if extra.relation_kind in ("spouse", "partner", "direct_sibling")
                else {(extra.subject_user_id, extra.object_user_id, extra.relation_kind)}
            )
            if keys & confirmed_keys:
                continue  # 与 confirmed 事实同结构：推测边不进入增广图
            fid = extra.fact_id
            if extra.relation_kind in _INFERENCE_PARENT_SUBTYPES:
                subtype = _INFERENCE_PARENT_SUBTYPES[extra.relation_kind]
                adjacency.setdefault(extra.object_user_id, []).append(
                    GraphEdge(extra.subject_user_id, EDGE_PARENT, subtype, "up", fid)
                )
                adjacency.setdefault(extra.subject_user_id, []).append(
                    GraphEdge(extra.object_user_id, EDGE_PARENT, subtype, "down", fid)
                )
            else:
                edge_type = (
                    EDGE_SPOUSE
                    if extra.relation_kind == "spouse"
                    else EDGE_PARTNER
                    if extra.relation_kind == "partner"
                    else EDGE_SIBLING
                )
                adjacency.setdefault(extra.subject_user_id, []).append(
                    GraphEdge(extra.object_user_id, edge_type, None, "sym", fid)
                )
                adjacency.setdefault(extra.object_user_id, []).append(
                    GraphEdge(extra.subject_user_id, edge_type, None, "sym", fid)
                )

    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge.to_id, _EDGE_ORDER[edge.edge_type], edge.fact_id))

    return RelationshipGraph(
        viewer_user_id=viewer_user_id,
        space_id=space_id,
        node_genders=genders,
        adjacency=adjacency,
        snapshot_hash=snapshot_hash,
        path_genders=path_genders,
        bridge_user_ids=frozenset(bridge_user_ids),
        confirmed_facts=confirmed_facts,
        bridges=tuple(bridge_snapshots),
        authorized_space_ids=frozenset(authorized_space_ids),
        valid_until=min(
            (bridge.expires_at for bridge in bridge_snapshots if bridge.expires_at is not None),
            default=None,
        ),
    )
