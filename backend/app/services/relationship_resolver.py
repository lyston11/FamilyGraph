"""确定性亲属路径解析器（V2.3 Block E2，KI-2 / AC-KI2 / AC-KI7）。

LLM 不参与本层：相同 (facts snapshot, algorithm_version) 产出逐字节相同的
concept/path/explanation。全部结论只由 confirmed SourceFact 的图快照推导。

## concept_code 编码合同（跨块合同，E3 TermRegistry 按 code 消费）

主路径规范化 step 序列 → 确定性字符串编码，规则（纯函数，黄金用例钉死在
tests/test_relationship_resolver.py）：

- 每步一个 token，token 之间以 ``-`` 连接；
- token = 边字母 + [亚型字母] + [性别字母]：
  - 边字母：``U``=parent up（上行为家长）、``D``=parent down（下行子女）、
    ``S``=spouse、``P``=partner、``B``=sibling；
  - 亚型字母（仅 parent 类边携带）：``a``=adoptive、``s``=step、
    ``g``=guardian；biological 无亚型字母；
  - 性别字母取该步目标节点（to 节点）的 gender：``m``/``f``；gender 为
    unknown 时省略性别字母；
- viewer == target 时 concept_code = ``SELF``；无路径时为 ``None``。

例：父亲=``Um``；母亲=``Uf``；爷爷=``Um-Um``；外公=``Uf-Um``；奶奶=``Um-Uf``；
奶奶的兄弟=``Um-Uf-Bm``；舅舅=``Uf-Bm``；养父=``Uam``；继母=``Usf``；儿子=``Dm``；
丈夫=``Sm``；妻子=``Sf``；伴侣=``Pm``；哥哥/弟弟=``Bm``。

## 主路径选择（确定性全序）

键 = (边数, 非血缘步数, 姻亲步数, 节点 id 序列字典序)：
① 最少边数；② parent 步优先于 sibling/spouse/partner（非血缘步少者胜，
血缘线优先）；③ 同分时 spouse/partner 步少者胜（姻亲次之；sibling 属血缘）；
④ 仍同分按路径节点 id 序列字典序。替代路径按同一键排序取次序 ≤3 条。

partner 未披露语义（AC-KI1）：partner 可以作为非 partner 前缀之后的末跳，
不得经 partner 继续延伸姻亲链（姻亲链仅允许经 spouse）。

## path_class 判定（按主路径，优先序自高到低）

self（viewer==target）→ none（无路径）→ step_adoptive（含 adoptive/step
parent 步）→ guardian（含 guardian 步）→ affinal（含 spouse/partner 步）→
collateral（含 sibling 步）→ direct_line（纯 parent 链）。
"""

from __future__ import annotations

import pickle
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from app.services.relationship_graph import FrozenMapping, RelationshipGraph, load_graph

# 深度上限（合同值）：超过 12 步的简单路径不再枚举（found=false）
MAX_PATH_DEPTH = 12
# 单对 (viewer, target) 枚举的简单路径总量上限（防病态图爆炸；确定性截断）
MAX_SIMPLE_PATHS = 128
ALT_PATH_LIMIT = 3
# Unlike the path count cap, these bound work and memory even when no path has
# been found. Exhaustion is an explicit failure, never a cached no-path answer.
MAX_SEARCH_EXPANSIONS = 2_000_000
MAX_SEARCH_STATE_BYTES = 8 * 1024 * 1024
SEARCH_SLICE_EXPANSIONS = 2048

PATH_CLASS_SELF = "self"
PATH_CLASS_NONE = "none"
PATH_CLASS_DIRECT_LINE = "direct_line"
PATH_CLASS_COLLATERAL = "collateral"
PATH_CLASS_AFFINAL = "affinal"
PATH_CLASS_STEP_ADOPTIVE = "step_adoptive"
PATH_CLASS_GUARDIAN = "guardian"


@dataclass(frozen=True)
class PathStep:
    """规范化单步：{from,to,edge_type,direction} + subtype/fact_id 依据。"""

    from_id: int
    to_id: int
    edge_type: str
    subtype: str | None
    direction: str
    fact_id: int

    def to_json(self) -> dict[str, Any]:
        """缓存行与工具输出的 JSON 形状（E3/E4 按此消费）。"""
        return {
            "from": self.from_id,
            "to": self.to_id,
            "edge_type": self.edge_type,
            "subtype": self.subtype,
            "direction": self.direction,
            "fact_id": self.fact_id,
        }


def steps_to_json(steps: tuple[PathStep, ...]) -> list[dict[str, Any]]:
    """PathStep 序列 → JSON 就绪列表。"""
    return [step.to_json() for step in steps]


@dataclass(frozen=True)
class RelationshipResolution:
    """resolve 的纯结构结果（无姓名等易变载荷，保证逐字节可复现）。"""

    viewer_user_id: int
    target_user_id: int
    space_id: int
    found: bool
    path_class: str
    concept_code: str | None
    main_path: tuple[PathStep, ...] = ()
    alt_paths: tuple[tuple[PathStep, ...], ...] = ()
    alt_descriptions: tuple[str, ...] = ()
    explanation_structural: str | None = None
    snapshot_hash: str = ""
    node_genders: Mapping[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_genders", FrozenMapping(self.node_genders))


# ---- 可让出的简单路径枚举（原迭代加深/邻接序，无 Session） ----


class SearchBudgetExceeded(RuntimeError):
    """A retryable/terminal budget outcome, distinct from a proved no-path.

    Positional exception args deliberately preserve fields across spawn/pickle.
    The coordinator owns retry policy; the resolver never silently raises limits.
    """

    def __init__(self, reason: str, expansions: int, limit: int) -> None:
        self.reason = reason
        self.expansions = expansions
        self.limit = limit
        super().__init__(reason, expansions, limit)

    def __str__(self) -> str:
        return f"relationship search exceeded {self.reason} budget ({self.limit})"


@dataclass
class _SearchFrame:
    node: int
    remaining_depth: int
    visited: frozenset[int]
    steps: tuple[PathStep, ...]
    edge_index: int = 0


@dataclass
class SearchState:
    """One detached continuation. Only its owning worker advances it.

    No generators, closures, ORM instances or process-local handles are retained.
    Saving/reloading a continuation preserves adjacency and iterative-depth order.
    Dropping it cancels a half-computed target without producing a result.
    """

    graph: RelationshipGraph
    target_user_id: int
    reverse_distances: dict[int, int]
    max_total_expansions: int
    max_state_bytes: int
    max_depth: int
    max_paths: int
    depth_limit: int = 1
    expansions: int = 0
    stack: list[_SearchFrame] = field(default_factory=list)
    paths: list[tuple[PathStep, ...]] = field(default_factory=list)
    complete: bool = False


@dataclass(frozen=True)
class SearchSlice:
    state: SearchState
    resolution: RelationshipResolution | None


def reachable_targets(graph: RelationshipGraph) -> dict[int, int]:
    """Authorized reachable node distances without enumerating alternative paths.

    A partner hop records a target but does not extend it. Keep non-partner
    distances separately: a node reached cheaply via partner can still have a
    longer valid non-partner prefix through which other targets are reachable.
    """
    start = graph.viewer_user_id
    distances = {start: 0}
    expandable = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        distance = expandable[node] + 1
        if distance > MAX_PATH_DEPTH:
            continue
        for edge in graph.adjacency.get(node, ()):
            if distance < distances.get(edge.to_id, MAX_PATH_DEPTH + 1):
                distances[edge.to_id] = distance
            if edge.edge_type != "partner" and edge.to_id not in expandable:
                expandable[edge.to_id] = distance
                queue.append(edge.to_id)
    return distances


def _reverse_distances(graph: RelationshipGraph, target: int) -> dict[int, int]:
    """A safe lower bound; only partner edges *into the goal* can be used.

    Ignoring the DFS visited set can underestimate distance, never overestimate
    it, so pruning cannot remove a path or change the first 128 path prefix.
    """
    incoming: dict[int, list[int]] = {}
    for node, edges in graph.adjacency.items():
        for edge in edges:
            if edge.edge_type != "partner" or edge.to_id == target:
                incoming.setdefault(edge.to_id, []).append(node)
    distances = {target: 0}
    queue = deque([target])
    while queue:
        node = queue.popleft()
        distance = distances[node] + 1
        if distance > MAX_PATH_DEPTH:
            continue
        for predecessor in incoming.get(node, ()):
            if predecessor not in distances:
                distances[predecessor] = distance
                queue.append(predecessor)
    return distances


def _check_state_budget(state: SearchState) -> None:
    if len(pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)) > state.max_state_bytes:
        raise SearchBudgetExceeded("state_bytes", state.expansions, state.max_state_bytes)


def _root_frame(state: SearchState) -> _SearchFrame:
    start = state.graph.viewer_user_id
    return _SearchFrame(start, state.depth_limit, frozenset({start}), ())


def start_search(
    graph: RelationshipGraph,
    *,
    target_user_id: int,
    max_total_expansions: int = MAX_SEARCH_EXPANSIONS,
    max_state_bytes: int = MAX_SEARCH_STATE_BYTES,
) -> SearchState:
    """Prepare a bounded pure search. Call advance_search until resolution exists."""
    if max_total_expansions < 1 or max_state_bytes < 1:
        raise ValueError("search budgets must be positive")
    if len(pickle.dumps(graph, protocol=pickle.HIGHEST_PROTOCOL)) > max_state_bytes:
        raise SearchBudgetExceeded("state_bytes", 0, max_state_bytes)
    distances = _reverse_distances(graph, target_user_id)
    depth_limit = distances.get(graph.viewer_user_id, MAX_PATH_DEPTH + 1)
    state = SearchState(
        graph=graph,
        target_user_id=target_user_id,
        reverse_distances=distances,
        max_total_expansions=max_total_expansions,
        max_state_bytes=max_state_bytes,
        max_depth=MAX_PATH_DEPTH,
        max_paths=MAX_SIMPLE_PATHS,
        depth_limit=max(1, depth_limit),
        complete=target_user_id == graph.viewer_user_id or depth_limit > MAX_PATH_DEPTH,
    )
    if not state.complete:
        state.stack.append(_root_frame(state))
    _check_state_budget(state)
    return state


def advance_search(
    state: SearchState, *, max_expansions: int = SEARCH_SLICE_EXPANSIONS
) -> SearchSlice:
    """Advance at most max_expansions adjacency entries, then yield explicitly.

    Frame pops/depth increments are bounded by MAX_PATH_DEPTH per expansion.
    Only completed searches expose a resolution; partially found paths do not
    qualify as a complete target. Total work and serialized state are bounded.
    """
    if max_expansions < 1:
        raise ValueError("slice expansion budget must be positive")
    slice_end = state.expansions + max_expansions
    while not state.complete:
        if not state.stack:
            if state.depth_limit >= state.max_depth or len(state.paths) >= state.max_paths:
                state.complete = True
                break
            state.depth_limit += 1
            state.stack.append(_root_frame(state))
        frame = state.stack[-1]
        edges = state.graph.adjacency.get(frame.node, ())
        if frame.edge_index >= len(edges):
            state.stack.pop()
            continue
        if state.expansions >= slice_end:
            break
        if state.expansions >= state.max_total_expansions:
            raise SearchBudgetExceeded("expansions", state.expansions, state.max_total_expansions)
        edge = edges[frame.edge_index]
        frame.edge_index += 1
        state.expansions += 1
        if edge.to_id in frame.visited:
            continue
        if edge.to_id == state.target_user_id:
            if frame.remaining_depth == 1:
                state.paths.append(
                    (
                        *frame.steps,
                        PathStep(
                            frame.node,
                            edge.to_id,
                            edge.edge_type,
                            edge.subtype,
                            edge.direction,
                            edge.fact_id,
                        ),
                    )
                )
                if len(state.paths) >= state.max_paths:
                    state.complete = True
            continue
        if frame.remaining_depth == 1 or edge.edge_type == "partner":
            continue
        lower_bound = state.reverse_distances.get(edge.to_id)
        if lower_bound is None or lower_bound > frame.remaining_depth - 1:
            continue
        steps = (
            *frame.steps,
            PathStep(
                frame.node,
                edge.to_id,
                edge.edge_type,
                edge.subtype,
                edge.direction,
                edge.fact_id,
            ),
        )
        state.stack.append(
            _SearchFrame(
                edge.to_id,
                frame.remaining_depth - 1,
                frame.visited | {edge.to_id},
                steps,
            )
        )
    _check_state_budget(state)
    resolution = (
        _resolution_for_paths(state.graph, state.target_user_id, state.paths)
        if state.complete
        else None
    )
    return SearchSlice(state=state, resolution=resolution)


def _enumerate_simple_paths(
    graph: RelationshipGraph, start: int, goal: int
) -> list[tuple[PathStep, ...]]:
    """Compatibility adapter over the same resumable engine used by Steward."""
    if start != graph.viewer_user_id:
        graph = replace(graph, viewer_user_id=start)
    state = start_search(graph, target_user_id=goal)
    while not state.complete:
        advance_search(state)
    return state.paths


def _path_sort_key(path: tuple[PathStep, ...]) -> tuple[Any, ...]:
    """主/替路径全序键：(边数, 非血缘步数, 姻亲步数, 节点 id 序列)。"""
    non_parent = sum(1 for step in path if step.edge_type != "parent")
    affinal = sum(1 for step in path if step.edge_type in ("spouse", "partner"))
    node_ids = (path[0].from_id, *(step.to_id for step in path))
    return (len(path), non_parent, affinal, node_ids)


# ---- concept_code 编码 ----

_SUBTYPE_LETTER = {"adoptive": "a", "step": "s", "guardian": "g"}
_GENDER_LETTER = {"m": "m", "f": "f"}
_SYM_LETTER = {"sibling": "B", "spouse": "S", "partner": "P", "bridge": "X"}


def _step_token(step: PathStep, genders: Mapping[int, str]) -> str:
    """单步编码：边字母 + [亚型字母（仅 parent 类）] + [性别字母]。"""
    if step.edge_type == "parent":
        token = "U" if step.direction == "up" else "D"
        if step.subtype:
            token += _SUBTYPE_LETTER.get(step.subtype, "")
    else:
        token = _SYM_LETTER[step.edge_type]
    return token + _GENDER_LETTER.get(genders.get(step.to_id, ""), "")


def concept_code_for_path(path: tuple[PathStep, ...], genders: Mapping[int, str]) -> str | None:
    """主路径 → concept_code（编码合同见模块 docstring）。"""
    if not path:
        return None
    return "-".join(_step_token(step, genders) for step in path)


# ---- path_class 与结构解释 ----


def path_class_for_path(
    *, viewer_user_id: int, target_user_id: int, path: tuple[PathStep, ...]
) -> str:
    """主路径 → path_class（判定优先序见模块 docstring）。"""
    if viewer_user_id == target_user_id:
        return PATH_CLASS_SELF
    if not path:
        return PATH_CLASS_NONE
    subtypes = {step.subtype for step in path}
    if "adoptive" in subtypes or "step" in subtypes:
        return PATH_CLASS_STEP_ADOPTIVE
    if "guardian" in subtypes:
        return PATH_CLASS_GUARDIAN
    if any(step.edge_type == "bridge" for step in path):
        return "cross_space"
    if any(step.edge_type in ("spouse", "partner") for step in path):
        return PATH_CLASS_AFFINAL
    if any(step.edge_type == "sibling" for step in path):
        return PATH_CLASS_COLLATERAL
    return PATH_CLASS_DIRECT_LINE


_PARENT_UP_ROLE = {
    ("biological", "m"): "父亲",
    ("biological", "f"): "母亲",
    ("biological", ""): "尊亲长",
    ("adoptive", "m"): "养父",
    ("adoptive", "f"): "养母",
    ("adoptive", ""): "养亲",
    ("step", "m"): "继父",
    ("step", "f"): "继母",
    ("step", ""): "继亲",
}
_PARENT_DOWN_ROLE = {
    ("biological", "m"): "儿子",
    ("biological", "f"): "女儿",
    ("biological", ""): "子女",
    ("adoptive", "m"): "养子",
    ("adoptive", "f"): "养女",
    ("adoptive", ""): "养子女",
    ("step", "m"): "继子",
    ("step", "f"): "继女",
    ("step", ""): "继子女",
}


def _step_role(step: PathStep, genders: Mapping[int, str]) -> str:
    """单步的确定性中文角色词（不依赖姓名，保证输出稳定）。"""
    suffix = _GENDER_LETTER.get(genders.get(step.to_id, ""), "")
    if step.edge_type == "parent" and step.direction == "up":
        if step.subtype == "guardian":
            return "监护人"
        return _PARENT_UP_ROLE.get((step.subtype or "biological", suffix), "尊亲长")
    if step.edge_type == "parent" and step.direction == "down":
        if step.subtype == "guardian":
            return "受监护子女"
        return _PARENT_DOWN_ROLE.get((step.subtype or "biological", suffix), "子女")
    return {
        ("spouse", "m"): "丈夫",
        ("spouse", "f"): "妻子",
        ("spouse", ""): "配偶",
        ("partner", "m"): "伴侣",
        ("partner", "f"): "伴侣",
        ("partner", ""): "伴侣",
        ("sibling", "m"): "兄弟",
        ("sibling", "f"): "姐妹",
        ("bridge", ""): "跨空间连接",
        ("bridge", "m"): "跨空间连接",
        ("bridge", "f"): "跨空间连接",
    }.get((step.edge_type, suffix), "亲属")


def describe_path(path: tuple[PathStep, ...], genders: Mapping[int, str]) -> str:
    """路径 → 确定性中文层级描述（「你的父亲的母亲的兄弟」式）。"""
    roles = "的".join(_step_role(step, genders) for step in path)
    return f"你的{roles}"


# ---- 入口 ----


def bulk_concept_codes(
    graph: RelationshipGraph,
    *,
    viewer_user_id: int,
    target_ids: Iterable[int],
) -> dict[int, str | None]:
    """批量计算 viewer→各目标的主路径 concept_code（E4a 候选图验证用）。

    主路径选择与编码完全复用 resolve_relationship 的同一批私有原语，
    保证与单点解析逐字节一致（单一真相，AC-KI7）。无路径目标得 None。
    """
    if graph.viewer_user_id != viewer_user_id:
        graph = replace(graph, viewer_user_id=viewer_user_id)
    return {
        target_user_id: resolve_graph(graph, target_user_id=target_user_id).concept_code
        for target_user_id in sorted(set(target_ids))
    }


def resolve_relationship(
    session: Session,
    *,
    viewer_user_id: int,
    target_user_id: int,
    space_id: int,
    extra_edges: tuple[Any, ...] | list[Any] | None = None,
) -> RelationshipResolution:
    """解析 viewer 在 space 内与 target 的亲属关系（确定性；AC-KI2/KI7）。

    ``extra_edges``（推测层，缺省 None = 行为不变）：透传给 load_graph 的
    推测单跳增广；含推测步的路径仍由本模块确定性枚举/编码/排序。
    """
    graph = load_graph(
        session,
        viewer_user_id=viewer_user_id,
        space_id=space_id,
        extra_edges=extra_edges,
    )
    return resolve_graph(graph, target_user_id=target_user_id)


def resolve_graph(graph: RelationshipGraph, *, target_user_id: int) -> RelationshipResolution:
    """Resolve a target using only an already-authorized, detached graph."""
    paths = _enumerate_simple_paths(graph, graph.viewer_user_id, target_user_id)
    return _resolution_for_paths(graph, target_user_id, paths)


def _resolution_for_paths(
    graph: RelationshipGraph, target_user_id: int, paths: list[tuple[PathStep, ...]]
) -> RelationshipResolution:
    viewer_user_id = graph.viewer_user_id
    space_id = graph.space_id

    if viewer_user_id == target_user_id:
        return RelationshipResolution(
            viewer_user_id=viewer_user_id,
            target_user_id=target_user_id,
            space_id=space_id,
            found=True,
            path_class=PATH_CLASS_SELF,
            concept_code="SELF",
            main_path=(),
            alt_paths=(),
            alt_descriptions=(),
            explanation_structural="这是你自己。",
            snapshot_hash=graph.snapshot_hash,
            node_genders=graph.node_genders,
        )

    if not paths:
        # 不泄露存在性：不可见/不存在/超深一律同一结果形状
        return RelationshipResolution(
            viewer_user_id=viewer_user_id,
            target_user_id=target_user_id,
            space_id=space_id,
            found=False,
            path_class=PATH_CLASS_NONE,
            concept_code=None,
            snapshot_hash=graph.snapshot_hash,
            node_genders=graph.node_genders,
        )

    ordered = sorted(paths, key=_path_sort_key)
    main_path = ordered[0]
    alts = tuple(ordered[1 : ALT_PATH_LIMIT + 1])
    genders = graph.node_genders
    return RelationshipResolution(
        viewer_user_id=viewer_user_id,
        target_user_id=target_user_id,
        space_id=space_id,
        found=True,
        path_class=path_class_for_path(
            viewer_user_id=viewer_user_id, target_user_id=target_user_id, path=main_path
        ),
        concept_code=concept_code_for_path(main_path, genders),
        main_path=main_path,
        alt_paths=alts,
        alt_descriptions=tuple(describe_path(path, genders) for path in alts),
        explanation_structural=describe_path(main_path, genders),
        snapshot_hash=graph.snapshot_hash,
        node_genders=genders,
    )
