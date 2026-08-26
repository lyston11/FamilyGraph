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

partner 未披露语义（AC-KI1）：partner 边只能作为单跳完整路径参与解析，
不得经 partner 继续延伸姻亲链（姻亲链仅允许经 spouse）。

## path_class 判定（按主路径，优先序自高到低）

self（viewer==target）→ none（无路径）→ step_adoptive（含 adoptive/step
parent 步）→ guardian（含 guardian 步）→ affinal（含 spouse/partner 步）→
collateral（含 sibling 步）→ direct_line（纯 parent 链）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.services.relationship_graph import RelationshipGraph, load_graph

# 深度上限（合同值）：超过 12 步的简单路径不再枚举（found=false）
MAX_PATH_DEPTH = 12
# 单对 (viewer, target) 枚举的简单路径总量上限（防病态图爆炸；确定性截断）
MAX_SIMPLE_PATHS = 128
ALT_PATH_LIMIT = 3

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
    node_genders: dict[int, str] = field(default_factory=dict)


# ---- 简单路径枚举（迭代加深 + 确定性邻接序，天然防环） ----


def _dfs_collect(
    graph: RelationshipGraph,
    node: int,
    goal: int,
    *,
    remaining_depth: int,
    visited: frozenset[int],
    steps: tuple[PathStep, ...],
    out: list[tuple[PathStep, ...]],
) -> None:
    """恰好 remaining_depth 步内到达 goal 的路径收集（每条路径只在其自身长度
    的迭代层入集一次）；邻接序已确定性排序。"""
    if len(out) >= MAX_SIMPLE_PATHS:
        return
    for edge in graph.adjacency.get(node, ()):
        if edge.to_id in visited:
            continue
        extended = (
            *steps,
            PathStep(node, edge.to_id, edge.edge_type, edge.subtype, edge.direction, edge.fact_id),
        )
        if edge.to_id == goal:
            if remaining_depth == 1:
                out.append(extended)
            continue
        if remaining_depth == 1:
            continue
        if edge.edge_type == "partner":
            # 未披露语义：partner 边不得延伸——姻亲链仅允许经 spouse（AC-KI1）
            continue
        _dfs_collect(
            graph,
            edge.to_id,
            goal,
            remaining_depth=remaining_depth - 1,
            visited=visited | {edge.to_id},
            steps=extended,
            out=out,
        )


def _enumerate_simple_paths(
    graph: RelationshipGraph, start: int, goal: int
) -> list[tuple[PathStep, ...]]:
    """start→goal 全部简单路径；迭代加深保证短路径先于长路径入集，
    达到总量上限时截断的必是更长路径（确定性截断）。"""
    out: list[tuple[PathStep, ...]] = []
    for depth_limit in range(1, MAX_PATH_DEPTH + 1):
        _dfs_collect(
            graph,
            start,
            goal,
            remaining_depth=depth_limit,
            visited=frozenset({start}),
            steps=(),
            out=out,
        )
        if len(out) >= MAX_SIMPLE_PATHS:
            break
    return out[:MAX_SIMPLE_PATHS]


def _path_sort_key(path: tuple[PathStep, ...]) -> tuple[Any, ...]:
    """主/替路径全序键：(边数, 非血缘步数, 姻亲步数, 节点 id 序列)。"""
    non_parent = sum(1 for step in path if step.edge_type != "parent")
    affinal = sum(1 for step in path if step.edge_type in ("spouse", "partner"))
    node_ids = (path[0].from_id, *(step.to_id for step in path))
    return (len(path), non_parent, affinal, node_ids)


# ---- concept_code 编码 ----

_SUBTYPE_LETTER = {"adoptive": "a", "step": "s", "guardian": "g"}
_GENDER_LETTER = {"m": "m", "f": "f"}
_SYM_LETTER = {"sibling": "B", "spouse": "S", "partner": "P"}


def _step_token(step: PathStep, genders: dict[int, str]) -> str:
    """单步编码：边字母 + [亚型字母（仅 parent 类）] + [性别字母]。"""
    if step.edge_type == "parent":
        token = "U" if step.direction == "up" else "D"
        if step.subtype:
            token += _SUBTYPE_LETTER.get(step.subtype, "")
    else:
        token = _SYM_LETTER[step.edge_type]
    return token + _GENDER_LETTER.get(genders.get(step.to_id, ""), "")


def concept_code_for_path(path: tuple[PathStep, ...], genders: dict[int, str]) -> str | None:
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


def _step_role(step: PathStep, genders: dict[int, str]) -> str:
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
        ("sibling", ""): "兄弟姐妹",
    }.get((step.edge_type, suffix), "亲属")


def describe_path(path: tuple[PathStep, ...], genders: dict[int, str]) -> str:
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
    out: dict[int, str | None] = {}
    for target_user_id in sorted(set(target_ids)):
        if target_user_id == viewer_user_id:
            out[target_user_id] = "SELF"
            continue
        paths = _enumerate_simple_paths(graph, viewer_user_id, target_user_id)
        out[target_user_id] = (
            concept_code_for_path(sorted(paths, key=_path_sort_key)[0], graph.node_genders)
            if paths
            else None
        )
    return out


def resolve_relationship(
    session: Session, *, viewer_user_id: int, target_user_id: int, space_id: int
) -> RelationshipResolution:
    """解析 viewer 在 space 内与 target 的亲属关系（确定性；AC-KI2/KI7）。"""
    graph = load_graph(session, viewer_user_id=viewer_user_id, space_id=space_id)

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

    paths = _enumerate_simple_paths(graph, viewer_user_id, target_user_id)
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
