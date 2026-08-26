"""AgentQueryService：V2.2 只读 Assistant 领域工具（任务 Block C1）。

把 Foundation 的 VisibilityPolicy 与现有 graph/profile/search 组合成稳定、
分页、有上限的只读工具；全部注册进 agent_tools 注册表（min_kind=assistant，
版本 @1）。合同要点：

- scope 一律取 Run 注入的 session（account_id + space_id），输入 schema 拒绝
  任意 actor/space 字段（注册表 additionalProperties=false）；
- purpose 固定 PURPOSE_AGENT：投影不得超过 profile API 口径（§0.1 收紧规则），
  投影复用 visibility.evaluate + payload_from_decision，不重写可见性规则；
- 零写入：六个工具只 SELECT，执行路径不 commit 业务表（审计由
  agent_tools.execute 既有逻辑负责）；
- 输出 JSON ≤8KB，超出截断并置 truncated=true；
- 分页 cursor 为 offset 语义，list_visible_people 总量上限 500 可见人。

本模块不得反向导入 agent_tools（保持依赖单向）；工具级协议拒绝以
QueryToolError 表达，由 agent_tools._dispatch 转译为 ToolProtocolError，
从而复用「拒绝码统一写安全审计」的既有路径。FG_PROFILE_NOT_AVAILABLE 属
正常工具结果（非协议违规），直接抛统一 API 错误、不走拒绝审计。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.errors import FG_PROFILE_NOT_AVAILABLE, raise_api_error
from app.models.account import Account
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.models.user import User
from app.services import visibility
from app.services.kinship import display_relation
from app.services.visibility import (
    CONTENT_FIELDS,
    FIELD_MASKED,
    LEVEL_SELF_PRIVATE,
    VisibilityDecision,
)

# ---- 工具名与输入 schema（共享合同：C2 sidecar / C3 前端按此对接，不得擅改） ----

TOOL_GET_SELF_CONTEXT = "familygraph.get_self_context"
TOOL_LIST_VISIBLE_PEOPLE = "familygraph.list_visible_people"
TOOL_GET_PROFILE_SUMMARY = "familygraph.get_profile_summary"
TOOL_SEARCH_SPACE = "familygraph.search_space"
TOOL_GET_RELATIONSHIP_PATH = "familygraph.get_relationship_path"
TOOL_EXPLAIN_STRUCTURAL_PATH = "familygraph.explain_structural_path"

QUERY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        TOOL_GET_SELF_CONTEXT,
        TOOL_LIST_VISIBLE_PEOPLE,
        TOOL_GET_PROFILE_SUMMARY,
        TOOL_SEARCH_SPACE,
        TOOL_GET_RELATIONSHIP_PATH,
        TOOL_EXPLAIN_STRUCTURAL_PATH,
    }
)

# additionalProperties=false 即「拒绝任意 actor/space 字段」的 fail-closed 实现；
# 子集校验器不支持 min/max/默认值，数值边界在服务层强制（_require_int_range）。
QUERY_TOOL_SPECS_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    TOOL_GET_SELF_CONTEXT: {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    TOOL_LIST_VISIBLE_PEOPLE: {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 50},
            "limit": {"type": "integer"},
            "cursor": {"type": "integer"},
        },
        "required": [],
        "additionalProperties": False,
    },
    TOOL_GET_PROFILE_SUMMARY: {
        "type": "object",
        "properties": {"user_id": {"type": "integer"}},
        "required": ["user_id"],
        "additionalProperties": False,
    },
    TOOL_SEARCH_SPACE: {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 64},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    TOOL_GET_RELATIONSHIP_PATH: {
        "type": "object",
        "properties": {
            "to_user_id": {"type": "integer"},
            "from_user_id": {"type": "integer"},
        },
        "required": ["to_user_id"],
        "additionalProperties": False,
    },
    TOOL_EXPLAIN_STRUCTURAL_PATH: {
        "type": "object",
        "properties": {
            "to_user_id": {"type": "integer"},
            "from_user_id": {"type": "integer"},
        },
        "required": ["to_user_id"],
        "additionalProperties": False,
    },
}

# ---- 上限常量 ----

OUTPUT_MAX_BYTES = 8 * 1024  # 单次输出 JSON 序列化上限
PEOPLE_SCAN_LIMIT = 500  # list_visible_people 可见人扫描总量上限
PATH_MAX_DEPTH = 6  # 关系路径 BFS 深度上限
GENERATION_MAX_DEPTH = 6  # 世代推导 BFS 深度上限
EXPLANATION_MAX_CHARS = 600  # 结构路径解释长度上限
LIST_DEFAULT_LIMIT = 20
LIST_MAX_LIMIT = 50
SEARCH_DEFAULT_LIMIT = 10
SEARCH_MAX_LIMIT = 20

_STRUCTURAL_CLASSES = ("elder", "younger", "spouse")
# dir_class → 解释文案角色（确定性中文生成，V2.3 前不做地方称谓推断）
_HOP_ROLES = {
    "elder": "长辈（长一辈）",
    "younger": "晚辈（晚一辈）",
    "spouse": "配偶（同辈）",
    "peer": "同辈（非确定结构关系）",
}
_GEN_DELTA = {"elder": 1, "younger": -1, "spouse": 0}


class QueryToolError(Exception):
    """领域工具级协议拒绝（与 agent_tools.ToolProtocolError 同构）。

    由 _dispatch 统一转译后走「拒绝码写安全审计」既有路径；不直接抛 HTTP。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: dict[str, object] | None = None,
    ):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


# ---- 入口 ----


def execute_query_tool(
    db: Session,
    *,
    agent_session: Any,
    name: str,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """按工具名分发到只读实现；scope 取自 AgentSession（Run token 已双向核验）。"""
    actor, space = _resolve_scope(db, agent_session)
    if name == TOOL_GET_SELF_CONTEXT:
        output = get_self_context(db, actor=actor, space=space)
    elif name == TOOL_LIST_VISIBLE_PEOPLE:
        output = list_visible_people(db, actor=actor, space_id=space.id, payload=input_payload)
    elif name == TOOL_GET_PROFILE_SUMMARY:
        output = get_profile_summary(db, actor=actor, space_id=space.id, payload=input_payload)
    elif name == TOOL_SEARCH_SPACE:
        output = search_space(db, actor=actor, space_id=space.id, payload=input_payload)
    elif name == TOOL_GET_RELATIONSHIP_PATH:
        output = get_relationship_path(db, actor=actor, space_id=space.id, payload=input_payload)
    else:  # TOOL_EXPLAIN_STRUCTURAL_PATH；注册表已保证名字合法
        output = explain_structural_path(db, actor=actor, space_id=space.id, payload=input_payload)
    return enforce_output_limit(output)


# ---- scope 解析（fail-closed 防御：FK 保证存在，异常即内部错误） ----


def _resolve_scope(db: Session, agent_session: Any) -> tuple[User, FamilySpace]:
    account = db.get(Account, agent_session.account_id)
    space = db.get(FamilySpace, agent_session.space_id)
    if account is None or space is None:  # pragma: no cover - FK 完整性防御
        raise QueryToolError(500, "INTERNAL_ERROR", "Run scope 实体缺失")
    actor = db.get(User, account.user_id)
    if actor is None:  # pragma: no cover - FK 完整性防御
        raise QueryToolError(500, "INTERNAL_ERROR", "Run scope 实体缺失")
    return actor, space


# ---- 输入范围校验（子集校验器不支持 min/max，边界在此强制） ----


def _require_int_range(
    field: str, value: Any, *, low: int, high: int, default: int | None = None
) -> int:
    if value is None:
        if default is None:
            raise QueryToolError(
                422, "AGENT_TOOL_SCHEMA_INVALID", "缺少必填字段", {"path": f"$.{field}"}
            )
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryToolError(
            422, "AGENT_TOOL_SCHEMA_INVALID", "字段须为 integer", {"path": f"$.{field}"}
        )
    if value < low or value > high:
        raise QueryToolError(
            422,
            "AGENT_TOOL_SCHEMA_INVALID",
            "字段超出允许范围",
            {"path": f"$.{field}", "allowed": f"{low}..{high}"},
        )
    return int(value)


# ---- 可见性与候选集合（全部走 visibility.evaluate 单点） ----


def _space_candidate_ids(db: Session, actor_id: int, space_id: int) -> list[int]:
    """当前空间 scope 的可见人候选：active 成员 + active 最小引用 + 本人。"""
    member_ids = set(
        db.scalars(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == space_id, SpaceMember.status == "active"
            )
        ).all()
    )
    ref_ids = set(
        db.scalars(
            select(SpaceProfileRef.user_id).where(
                SpaceProfileRef.space_id == space_id, SpaceProfileRef.status == "active"
            )
        ).all()
    )
    return sorted(member_ids | ref_ids | {actor_id})


def _visible_people(
    db: Session, actor: User, space_id: int
) -> list[tuple[User, VisibilityDecision]]:
    """空间内可见人物（purpose=agent 口径），按 user_id 稳定排序、封顶扫描量。"""
    out: list[tuple[User, VisibilityDecision]] = []
    for uid in _space_candidate_ids(db, actor.id, space_id):
        target = db.get(User, uid)
        if target is None:
            continue
        decision = visibility.evaluate(
            db, actor, target, space_context=space_id, purpose=visibility.PURPOSE_AGENT
        )
        if decision.visible:
            out.append((target, decision))
    out.sort(key=lambda pair: pair[0].id)
    return out[:PEOPLE_SCAN_LIMIT]


def _fact_state(decision: VisibilityDecision, target: User) -> str:
    """fact_state 合同值映射：confirmed | provisional | masked。

    - 未完成确档 → provisional（模型须声明该人物事实未经确认）；
    - 已确档但内容字段全部遮蔽 → masked（模型不可就该人物主张任何事实）；
    - 其余 → confirmed。
    """
    if target.profile_status != "identity_confirmed":
        return "provisional"
    if decision.level != LEVEL_SELF_PRIVATE and all(
        decision.fields.get(field) == FIELD_MASKED for field in CONTENT_FIELDS
    ):
        return "masked"
    return "confirmed"


def _generation_map(db: Session, start_id: int, candidate_ids: Iterable[int]) -> dict[int, int]:
    """从 start 沿 active 结构边（elder/younger/spouse）BFS 推导相对世代。

    方向语义（models/relation.py）：to_user 是 from_user 的 dir_class —— elder 边
    即 to_user 比 from_user 长一辈。多路径取最短跳数（先到先得，不回填）。
    """
    allowed = set(candidate_ids)
    gens: dict[int, int] = {start_id: 0}
    frontier = [start_id]
    depth = 0
    while frontier and depth < GENERATION_MAX_DEPTH:
        edges = db.scalars(
            select(Relation).where(
                Relation.status == "active",
                Relation.dir_class.in_(_STRUCTURAL_CLASSES),
                or_(Relation.from_user.in_(frontier), Relation.to_user.in_(frontier)),
            )
        ).all()
        next_frontier: list[int] = []
        for edge in edges:
            forward_delta = _GEN_DELTA[edge.dir_class]
            for src, dst, delta in (
                (edge.from_user, edge.to_user, forward_delta),
                (edge.to_user, edge.from_user, -forward_delta),
            ):
                if src in gens and dst in allowed and dst not in gens:
                    gens[dst] = gens[src] + delta
                    next_frontier.append(dst)
        frontier = next_frontier
        depth += 1
    return gens


def _direct_edge_label(db: Session, self_id: int, other_id: int) -> str | None:
    """本人与目标的直接 active 边称谓（viewer 视角反译，label 保留创建者原文 D3）。"""
    edge = db.scalar(
        select(Relation)
        .where(
            Relation.status == "active",
            or_(
                (Relation.from_user == self_id) & (Relation.to_user == other_id),
                (Relation.to_user == self_id) & (Relation.from_user == other_id),
            ),
        )
        .order_by(Relation.id)
        .limit(1)
    )
    if edge is None:
        return None
    dir_class, label, _from_creator = display_relation(edge, self_id)
    return label or dir_class


# ---- 关系路径 BFS（两端点及途经点均须对 actor 可见，防存在性泄露） ----


def _find_visible_path(
    db: Session, actor: User, space_id: int, start_id: int, goal_id: int
) -> tuple[bool, str, list[dict[str, Any]], list[int]]:
    """返回 (found, path_class, hops, evidence_relation_ids)。

    BFS 只经过「actor 在当前空间 scope 下 purpose=agent 可见」的节点；终点
    不可见/不存在时返回 found=False 且不泄露对方存在性。hops 元素：
    {user_id, name, dir_class(上一节点视角), label?(创建者原文)}。
    """
    if start_id == goal_id:  # 同一人物无路径语义
        return False, "none", [], []

    def _visible(uid: int) -> bool:
        target = db.get(User, uid)
        if target is None:
            return False
        if uid == actor.id:
            return True
        decision = visibility.evaluate(
            db, actor, target, space_context=space_id, purpose=visibility.PURPOSE_AGENT
        )
        return decision.visible

    hidden: set[int] = set()

    def _traversable(uid: int) -> bool:
        if uid in hidden:
            return False
        if _visible(uid):
            return True
        hidden.add(uid)
        return False

    if not (_traversable(start_id) and _traversable(goal_id)):
        return False, "none", [], []

    visited: set[int] = {start_id}
    parents: dict[int, tuple[int, Relation]] = {}  # node -> (prev_node, edge)
    frontier = [start_id]
    depth = 0
    while frontier and depth < PATH_MAX_DEPTH and goal_id not in parents:
        edges = db.scalars(
            select(Relation).where(
                Relation.status == "active",
                or_(Relation.from_user.in_(frontier), Relation.to_user.in_(frontier)),
            )
        ).all()
        neighbors: list[int] = []
        for edge in edges:
            pairs = [(edge.from_user, edge.to_user), (edge.to_user, edge.from_user)]
            for src, dst in pairs:
                if src not in frontier or dst in visited:
                    continue
                if not _traversable(dst):
                    continue
                visited.add(dst)
                parents[dst] = (src, edge)
                neighbors.append(dst)
        frontier = neighbors
        depth += 1

    if goal_id not in parents:
        return False, "none", [], []

    chain: list[tuple[int, Relation]] = []
    cursor = goal_id
    while cursor != start_id:
        prev, edge = parents[cursor]
        chain.append((cursor, edge))
        cursor = prev
    chain.reverse()

    hops: list[dict[str, Any]] = []
    evidence: list[int] = []
    prev_node = start_id
    for node, edge in chain:
        dir_class, label, _creator = display_relation(edge, prev_node)
        node_row = db.get(User, node)
        hop: dict[str, Any] = {
            "user_id": node,
            "name": node_row.name if node_row is not None else "",
            "dir_class": dir_class,
        }
        if label:
            hop["label"] = label
        hops.append(hop)
        evidence.append(edge.id)
        prev_node = node
    path_class = "direct" if len(hops) == 1 else "multi_hop"
    return True, path_class, hops, evidence


def _path_generation_delta(hops: list[dict[str, Any]]) -> int:
    """沿 hop 序列累计世代差（起点相对 0）。"""
    return sum(_GEN_DELTA.get(str(hop["dir_class"]), 0) for hop in hops)


# ---- 六个只读工具实现 ----


def get_self_context(db: Session, *, actor: User, space: FamilySpace) -> dict[str, Any]:
    """当前空间/本人摘要：scope 横幅数据源（UI「正在询问哪个空间」）。"""
    people = _visible_people(db, actor, space.id)
    return {
        "space_id": space.id,
        "space_name": space.name,
        "space_kind": space.kind,
        "self_user_id": actor.id,
        "self_name": actor.name,
        "profile_status": actor.profile_status,
        "visible_people_count": len(people),
    }


def list_visible_people(
    db: Session, *, actor: User, space_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """列出当前空间可见人物：offset 分页 + 名字过滤 + 相对世代/直连称谓。"""
    limit = _require_int_range(
        "limit", payload.get("limit"), low=1, high=LIST_MAX_LIMIT, default=LIST_DEFAULT_LIMIT
    )
    cursor = _require_int_range("cursor", payload.get("cursor"), low=0, high=10**9, default=0)
    query = payload.get("query")

    people = _visible_people(db, actor, space_id)
    if isinstance(query, str) and query:
        people = [(user, decision) for user, decision in people if query in user.name]

    generations = _generation_map(db, actor.id, {user.id for user, _decision in people})
    window = people[cursor : cursor + limit]
    entries = [
        {
            "user_id": user.id,
            "name": user.name,
            "generation": generations.get(user.id),
            "relation_to_self": (
                None if user.id == actor.id else _direct_edge_label(db, actor.id, user.id)
            ),
            "fact_state": _fact_state(decision, user),
        }
        for user, decision in window
    ]
    next_offset = cursor + len(window)
    return {
        "people": entries,
        "next_cursor": next_offset if next_offset < len(people) else None,
    }


def get_profile_summary(
    db: Session, *, actor: User, space_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """单人档案投影：完全复用 VisibilityPolicy 投影原语，附加层级与事实状态。

    与 users 路由同一组合（evaluate + payload_from_decision），仅绑定
    space_context 与 purpose=agent 收紧口径。
    """
    user_id = _require_int_range("user_id", payload.get("user_id"), low=1, high=10**9)
    target = db.get(User, user_id)
    decision = (
        visibility.evaluate(
            db, actor, target, space_context=space_id, purpose=visibility.PURPOSE_AGENT
        )
        if target is not None
        else None
    )
    if target is None or decision is None or not decision.visible:
        # 不存在与不可见同码同文案（防枚举；语义同 users 路由 none→404）
        raise_api_error(404, FG_PROFILE_NOT_AVAILABLE, "档案不可用或不存在")
    projection = visibility.payload_from_decision(decision, target)
    created_at = projection.get("created_at")
    if isinstance(created_at, datetime):
        projection["created_at"] = created_at.isoformat()
    projection["visibility_level"] = decision.level
    projection["fact_state"] = _fact_state(decision, target)
    return projection


def search_space(
    db: Session, *, actor: User, space_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """空间内搜索：口径对齐 misc.search（名字前缀/中缀 + 称谓标签前缀），
    候选与可见性均限定在当前空间 scope（purpose=agent）。"""
    raw_query = payload.get("query")
    if not isinstance(raw_query, str) or not (1 <= len(raw_query) <= 64):
        raise QueryToolError(
            422, "AGENT_TOOL_SCHEMA_INVALID", "query 须为 1..64 字符", {"path": "$.query"}
        )
    limit = _require_int_range(
        "limit", payload.get("limit"), low=1, high=SEARCH_MAX_LIMIT, default=SEARCH_DEFAULT_LIMIT
    )

    visible = _visible_people(db, actor, space_id)
    by_id = {user.id: user for user, _decision in visible}

    hits: list[dict[str, Any]] = []
    matched: set[int] = set()
    for user, _decision in sorted(visible, key=lambda pair: (pair[0].name, pair[0].id)):
        if raw_query in user.name:
            hits.append({"user_id": user.id, "name": user.name, "matched_field": "name"})
            matched.add(user.id)

    # 称谓标签匹配对齐 misc.search：仅 actor 为端点的 active 边，label 前缀命中
    label_edges = db.scalars(
        select(Relation).where(
            Relation.status == "active",
            Relation.label.like(f"{raw_query}%"),
            or_(Relation.from_user == actor.id, Relation.to_user == actor.id),
        )
    ).all()
    for edge in label_edges:
        other = edge.to_user if edge.from_user == actor.id else edge.from_user
        if other in matched or other not in by_id:
            continue
        hits.append(
            {"user_id": other, "name": by_id[other].name, "matched_field": "relation_label"}
        )
        matched.add(other)

    return {"hits": hits[:limit]}


def get_relationship_path(
    db: Session, *, actor: User, space_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """两人可见关系路径：结构边 BFS（≤6 跳），跨空间不可达一律 found=false。"""
    to_user_id = _require_int_range("to_user_id", payload.get("to_user_id"), low=1, high=10**9)
    from_user_id = _require_int_range(
        "from_user_id", payload.get("from_user_id"), low=1, high=10**9, default=actor.id
    )
    found, path_class, hops, evidence = _find_visible_path(
        db, actor, space_id, from_user_id, to_user_id
    )
    return {
        "found": found,
        "path_class": path_class,
        "path": hops,
        "evidence_relation_ids": evidence,
    }


def explain_structural_path(
    db: Session, *, actor: User, space_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """解释已确定的结构路径：服务端确定性中文生成，逐跳给依据；非确定进 caveats。

    V2.3 前只解释确定结构路径（elder/younger/spouse）；peer 跳与未确档参与者
    一律写入 caveats 提示，不据此生成称谓结论。
    """
    to_user_id = _require_int_range("to_user_id", payload.get("to_user_id"), low=1, high=10**9)
    from_user_id = _require_int_range(
        "from_user_id", payload.get("from_user_id"), low=1, high=10**9, default=actor.id
    )
    found, path_class, hops, evidence = _find_visible_path(
        db, actor, space_id, from_user_id, to_user_id
    )

    if not found:
        return {
            "explanation": "在当前空间的可见范围内，没有找到两人之间可验证的关系路径。",
            "path_class": path_class,
            "caveats": ["资料不足：未经验证的推测不会作为确认事实回答。"],
        }

    participant_ids = {from_user_id, to_user_id} | {hop["user_id"] for hop in hops}
    participants = db.query(User).filter(User.id.in_(participant_ids)).all()
    names = {user.id: user.name for user in participants}

    start_name = names.get(from_user_id, "")
    end_name = names.get(to_user_id, "")
    hop_texts: list[str] = []
    has_peer = False
    prev_node = from_user_id
    for index, hop in enumerate(hops):
        role = _HOP_ROLES.get(str(hop["dir_class"]), "亲属")
        has_peer = has_peer or hop["dir_class"] == "peer"
        hop_texts.append(
            f"{hop['name']} 是 {names.get(prev_node, '')} 的{role}（关系记录 #{evidence[index]}）"
        )
        prev_node = hop["user_id"]

    generation = _path_generation_delta(hops)
    gen_text = f"第 {generation:+d} 代" if generation != 0 else "同一代"
    explanation = (
        f"从「{start_name}」到「{end_name}」共 {len(hops)} 跳："
        + "；".join(hop_texts)
        + f"。以「{start_name}」为参照世代，「{end_name}」为{gen_text}。"
    )

    caveats: list[str] = []
    if has_peer:
        caveats.append("路径包含同辈等非确定结构关系，暂不据此推断具体地方称谓。")
    for user in sorted(participants, key=lambda item: item.id):
        if user.profile_status != "identity_confirmed":
            caveats.append(f"「{user.name}」的档案尚未完成确档，相关表述以确档结果为准。")

    return {
        "explanation": explanation[:EXPLANATION_MAX_CHARS],
        "path_class": path_class,
        "caveats": caveats,
    }


# ---- 输出大小上限（≤8KB；超限截列表并置 truncated） ----


def _json_size(output: dict[str, Any]) -> int:
    return len(json.dumps(output, ensure_ascii=False, default=str).encode("utf-8"))


def enforce_output_limit(output: dict[str, Any]) -> dict[str, Any]:
    """JSON 序列化 ≤8KB：优先自尾部截断列表字段，仍超限时截断长字符串。"""
    if _json_size(output) <= OUTPUT_MAX_BYTES:
        return output
    trimmed = dict(output)
    for key in [k for k, v in trimmed.items() if isinstance(v, list)]:
        items = list(trimmed[key])
        while items and _json_size({**trimmed, key: items}) > OUTPUT_MAX_BYTES:
            items.pop()
        trimmed[key] = items
    for key in [k for k, v in trimmed.items() if isinstance(v, str)]:
        while trimmed[key] and _json_size(trimmed) > OUTPUT_MAX_BYTES:
            trimmed[key] = trimmed[key][:-64]
    trimmed["truncated"] = True
    return trimmed


__all__ = [
    "QUERY_TOOL_NAMES",
    "QUERY_TOOL_SPECS_INPUT_SCHEMAS",
    "QueryToolError",
    "TOOL_EXPLAIN_STRUCTURAL_PATH",
    "TOOL_GET_PROFILE_SUMMARY",
    "TOOL_GET_RELATIONSHIP_PATH",
    "TOOL_GET_SELF_CONTEXT",
    "TOOL_LIST_VISIBLE_PEOPLE",
    "TOOL_SEARCH_SPACE",
    "enforce_output_limit",
    "execute_query_tool",
]
