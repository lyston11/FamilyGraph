"""版本化领域工具注册表与执行门禁（RT-3 / notes.md）。

骨架协议工具（echo、probe_scope）验证「token scope → 注册表
校验 → 服务层执行 → 审计」全链路；V2.2 起六个只读领域工具（AgentQueryService，
见 services/agent_query.py）以 required_kind=assistant 注册。严格校验 fail-closed，
四类拒绝码均写安全审计：
- 未知工具        → AGENT_TOOL_UNKNOWN
- 版本不匹配      → AGENT_TOOL_VERSION_UNSUPPORTED
- schema 违规     → AGENT_TOOL_SCHEMA_INVALID（额外字段/类型/必填）
- allowlist/kind  → AGENT_TOOL_SCOPE_DENIED

执行本身也留审计（只记工具名/版本/run/attempt，不记输入输出 payload）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, NoReturn, cast

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, select, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    AGENT_KIND_UNSUPPORTED,
    AGENT_TOOL_CALL_CONFLICT,
    AGENT_TOOL_CALL_IN_PROGRESS,
    KINSHIP_FLAG_DISABLED,
    WEB_TOOL_DISABLED,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import RUNTIME_AGENT_KINDS, AgentRun, AgentSession, AgentToolCall
from app.models.user import User
from app.services import (
    agent_query,
    audit,
    controlled_web,
    intake_extractor,
    platform_features,
    steward_memory,
    steward_tools,
    terms,
)
from app.services.agent_execution import (
    ExecutionIdentity,
    StewardExecution,
    fence_assistant_execution,
    fence_steward_execution,
)
from app.utils import timeutil

# JSON schema 子集校验支持的标量类型
_SUPPORTED_TYPES = {"string", "integer", "boolean", "array", "object"}


class ToolProtocolError(Exception):
    """工具协议拒绝（携带 HTTP 语义）；由 execute() 统一转审计 + API 错误。"""

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


@dataclass(frozen=True)
class ToolSpec:
    """注册表条目：名称/版本/schema/error codes/最低 kind 全部版本化披露。"""

    name: str
    version: int
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    error_codes: tuple[str, ...] = field(default=())
    required_kind: str | None = None  # None = 当前 Assistant runtime 均可用
    # 兼容旧调用方仍可请求的版本集合；None = 仅当前 version。
    # V2.3：两个关系工具升 @2（Relationship Intelligence 解析），保留 @1 声明，
    # sidecar 未跟进升级前继续以 @1 调用（E4 才切换）。
    supported_versions: tuple[int, ...] | None = None


TOOL_ECHO = "familygraph.echo"
TOOL_PROBE_SCOPE = "familygraph.probe_scope"

# V2.3 Block E4a：Relationship Intelligence 内部工具（required_kind=assistant，@1）。
# RELATIONSHIP_INTELLIGENCE_ENABLED 关闭时一律拒绝（与浏览器面 503 同一口径）。
TOOL_RESOLVE_FREE_TEXT_RELATION = "familygraph.resolve_free_text_relation"
TOOL_GET_TERM_ALTERNATIVES = "familygraph.get_term_alternatives"
TOOL_RECORD_TERM_USAGE = "familygraph.record_term_usage"
# 受控记忆工具（P2-b）。`search_memory` 只读；`propose_memory` **只写 pending 候选**。
# 两者都是 `required_kind="assistant"`：记忆是账号级私有事实，Steward 是共享数据
# 策略消费者，不得触碰（与 private branch 的排除口径一致）。
TOOL_SEARCH_MEMORY = "familygraph.search_memory"
TOOL_PROPOSE_MEMORY = "familygraph.propose_memory"
TOOL_SEARCH_WEB = "familygraph.search_web"
TOOL_FETCH_APPROVED_PAGE = "familygraph.fetch_approved_page"
_KINSHIP_INTAKE_TOOLS = frozenset(
    {TOOL_RESOLVE_FREE_TEXT_RELATION, TOOL_GET_TERM_ALTERNATIVES, TOOL_RECORD_TERM_USAGE}
)

# V2.2 只读 Assistant 领域工具（合同与 schema 权威在 services/agent_query.py）
# V2.3：两个关系工具升 @2 —— flag 开启时输出 additive 扩展字段
# （concept_code/path_class 新词表/alt_paths/evidence_fact_ids/algorithm_version），
# input schema 不变；@1 继续受理（sidecar E4 才跟进），flag 关闭时行为与 @1 相同。
_QUERY_TOOL_DESCRIPTIONS = {
    agent_query.TOOL_GET_SELF_CONTEXT: "当前空间/本人摘要（scope 横幅数据源，只读）",
    agent_query.TOOL_LIST_VISIBLE_PEOPLE: "列出当前空间可见人物（offset 分页，只读）",
    agent_query.TOOL_GET_PROFILE_SUMMARY: "读取可见档案投影（VisibilityPolicy 投影，只读）",
    agent_query.TOOL_SEARCH_SPACE: "在当前空间 scope 内搜索人物（只读）",
    agent_query.TOOL_GET_RELATIONSHIP_PATH: (
        "取得两人可见关系路径（@2 起 Relationship Intelligence 确定性解析，只读）"
    ),
    agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH: (
        "解释两人结构关系并给出依据与替代路径（@2 起新解析器，只读）"
    ),
}

# 升 @2 并保留 @1 兼容声明的工具（V2.3 Relationship Intelligence）
_KINSHIP_TOOL_VERSIONS: dict[str, tuple[int, ...]] = {
    agent_query.TOOL_GET_RELATIONSHIP_PATH: (1, 2),
    agent_query.TOOL_EXPLAIN_STRUCTURAL_PATH: (1, 2),
}


def _query_tool_specs() -> tuple[ToolSpec, ...]:
    """六个版本化只读领域工具的注册表条目（required_kind=assistant）。"""
    specs = []
    for name in sorted(agent_query.QUERY_TOOL_NAMES):
        versions = _KINSHIP_TOOL_VERSIONS.get(name)
        specs.append(
            ToolSpec(
                name=name,
                version=max(versions) if versions else 1,
                description=_QUERY_TOOL_DESCRIPTIONS[name],
                input_schema=agent_query.QUERY_TOOL_SPECS_INPUT_SCHEMAS[name],
                output_schema={"type": "object"},
                required_kind="assistant",
                supported_versions=versions,
            )
        )
    return tuple(specs)


def _steward_tool_specs() -> tuple[ToolSpec, ...]:
    descriptions = {
        steward_tools.TOOL_GET_SPACE_SNAPSHOT: "读取当前空间已发布的 Steward 投影快照元数据",
        steward_tools.TOOL_LIST_SPACE_NODES: "读取当前空间已发布投影的稳定节点摘要",
        steward_tools.TOOL_GET_VIEWER_TARGET: "读取当前 viewer 对目标的已发布投影",
        steward_tools.TOOL_GET_VIEWER_TERM: "读取当前 viewer 的已发布称谓投影",
        steward_tools.TOOL_GET_EVIDENCE: "读取当前发布视图允许引用的结构化证据",
        steward_tools.TOOL_GET_RELATIONSHIP_PATH: "读取当前空间已发布的关系路径",
        steward_tools.TOOL_SEARCH_MEMORY: (
            "在当前空间**配置允许的级别**内检索已确认的记忆（只读，句柄 + 摘要；"
            "不含原文；private 仅在带 viewer 的 attempt 上可见）"
        ),
    }
    return tuple(
        ToolSpec(
            name=name,
            version=1,
            description=descriptions[name],
            input_schema=steward_tools.STEWARD_TOOL_INPUT_SCHEMAS[name],
            output_schema={"type": "object"},
            required_kind="steward",
        )
        for name in sorted(steward_tools.STEWARD_TOOL_NAMES)
    )


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        *_query_tool_specs(),
        *_steward_tool_specs(),
        ToolSpec(
            name=TOOL_ECHO,
            version=1,
            description="回显输入文本（协议连通性测试，无副作用）",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 1000}},
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            # Assistant 与 Steward 都使用显式 required_kind，避免任一侧
            # 因默认遍历意外继承另一侧工具。
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_SEARCH_MEMORY,
            version=1,
            description=(
                "在当前会话空间内检索已确认的记忆与授权资料，返回可引用句柄。"
                "回答前可先用它自查是否已知某件事，避免重复询问用户。只读。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 500},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_PROPOSE_MEMORY,
            version=1,
            description=(
                "当用户明确要求『记住这件事』时，提议一条待确认记忆。"
                "只创建待确认候选，**不会**成为可检索记忆；用户仍需在记忆面板确认。"
                "原文由服务端从本轮用户消息取，不要改写原话。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "maxLength": 2000},
                    "purpose": {"type": "string", "maxLength": 120},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_PROBE_SCOPE,
            version=1,
            description="返回 run token 与 DB 双向核验后的 scope 摘要（授权链路证明）",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            output_schema={"type": "object", "properties": {}},
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_RESOLVE_FREE_TEXT_RELATION,
            version=1,
            description=(
                "确定性解析自由文本亲属称谓（如『奶奶的兄弟』），"
                "返回四级 resolution 结果；space 取会话空间，只读不写事实"
            ),
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 80}},
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_GET_TERM_ALTERNATIVES,
            version=1,
            description=("列出某概念码的可用叫法（个人偏好单列 + 空间/语言包/系统替代项），只读"),
            input_schema={
                "type": "object",
                "properties": {
                    "concept_code": {"type": "string", "maxLength": 128},
                    "limit": {"type": "integer"},
                },
                "required": ["concept_code"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_RECORD_TERM_USAGE,
            version=1,
            description=(
                "记录当前用户在某空间使用某叫法（source_event=assistant_query），"
                "返回两人晋升规则重算结果；同账号重复只计一次"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "concept_code": {"type": "string", "maxLength": 128},
                    "term": {"type": "string", "maxLength": 64},
                    "consent_confirmed": {"type": "boolean"},
                },
                "required": ["concept_code", "term", "consent_confirmed"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_SEARCH_WEB,
            version=1,
            description="在当前已授权空间内搜索受控联网 Provider（结果不可信，仅返回批准凭据）",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 500},
                    "use_case": {"type": "string", "maxLength": 32},
                    "limit": {"type": "integer"},
                },
                "required": ["query", "use_case"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            error_codes=(WEB_TOOL_DISABLED,),
            required_kind="assistant",
        ),
        ToolSpec(
            name=TOOL_FETCH_APPROVED_PAGE,
            version=1,
            description="抓取当前空间搜索结果批准的一次性 URL，不接受任意网址",
            input_schema={
                "type": "object",
                "properties": {
                    "approved_token": {"type": "string", "maxLength": 256},
                },
                "required": ["approved_token"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            error_codes=(WEB_TOOL_DISABLED,),
            required_kind="assistant",
        ),
    )
}


def resolve_tool(name: str, version: int) -> ToolSpec:
    """未知工具/版本一律拒绝（RT-3：版本化发现合同；兼容集见 ToolSpec）。"""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolProtocolError(404, "AGENT_TOOL_UNKNOWN", "未知工具", {"tool": name})
    supported = spec.supported_versions or (spec.version,)
    if version not in supported:
        raise ToolProtocolError(
            400,
            "AGENT_TOOL_VERSION_UNSUPPORTED",
            "工具版本不受支持",
            {"tool": name, "supported_versions": list(supported)},
        )
    return spec


def default_allowlist(
    kind: str,
    db: Session | None = None,
    *,
    account_id: int | None = None,
    space_id: int | None = None,
    viewer_scope: bool = False,
) -> list[str]:
    """Return the run's default allowlist, including Web only after both opt-ins.

    The optional database scope keeps backwards compatibility for tests and the
    protocol fixtures while ensuring a real run never advertises disabled Web tools.

    ``viewer_scope`` (steward only) gates the two viewer-bound tools. They require
    an ``viewer_account_id`` claim, which only terminology attempts carry, so
    advertising them to a candidate/ranking run produces a tool the model can see
    and call but that is guaranteed to be refused — measured as 476 rejections
    across 60 runs before this gate existed. A run must not advertise a capability
    it cannot use.
    """
    if kind not in RUNTIME_AGENT_KINDS:
        raise ToolProtocolError(
            422,
            AGENT_KIND_UNSUPPORTED,
            "Agent Runtime 只支持 Assistant 与 Steward",
            {"kind": kind},
        )
    # Web tools are opt-in by policy; exclude them from the static traversal so a
    # disabled platform/space flag never advertises them to the model.
    _web_tools = {TOOL_SEARCH_WEB, TOOL_FETCH_APPROVED_PAGE}
    # viewer-bound steward tools are only usable when the run carries a viewer claim;
    # advertising them otherwise guarantees a 403 the model cannot recover from.
    _unusable = set(
        steward_tools.STEWARD_VIEWER_TOOL_NAMES if kind == "steward" and not viewer_scope else ()
    )
    # 记忆工具同理：平台未启用记忆（或未启用检索）时 `search_rag` / `propose_candidate`
    # 会直接拒绝。广告一个必然被拒的工具只会消耗一轮模型调用并让它误判能力。
    # 无法判定（无 db 作用域）时**不**广告：宁可少给，不可给出必然失败的调用。
    # 注意两个工具的门禁条件不同——`propose_memory` 只要求记忆启用，
    # `search_memory` 还要求 RAG 启用（检索未启用时 search_rag 会拒）。
    if kind == "assistant":
        if db is None:
            _unusable.update({TOOL_SEARCH_MEMORY, TOOL_PROPOSE_MEMORY})
        else:
            if not platform_features.is_memory_enabled(db):
                _unusable.update({TOOL_SEARCH_MEMORY, TOOL_PROPOSE_MEMORY})
            elif not platform_features.is_rag_enabled(db):
                _unusable.add(TOOL_SEARCH_MEMORY)
    elif kind == "steward":
        # 管家记忆工具同理：可读集为空（或检索未启用）时 `_search_memory` 必然拒绝。
        # 可读集 = env ∩ 平台列 ∩ 空间列，且**无 viewer 时去掉 private**，所以
        # `viewer_scope` 参与判定：只配了 private 的 run 拿不到它。
        if db is None or space_id is None or not platform_features.is_rag_enabled(db):
            _unusable.add(steward_tools.TOOL_SEARCH_MEMORY)
        else:
            scopes = steward_memory.effective_scopes(db, space_id=space_id)
            if not viewer_scope:
                scopes = tuple(scope for scope in scopes if scope != "private")
            if not scopes:
                _unusable.add(steward_tools.TOOL_SEARCH_MEMORY)
    allowlist = sorted(
        name
        for name, spec in REGISTRY.items()
        if (spec.required_kind is None or spec.required_kind == kind)
        and name not in _web_tools
        and name not in _unusable
    )
    if (
        kind == "assistant"
        and db is not None
        and account_id is not None
        and space_id is not None
        and controlled_web.agent_tools_enabled(db, account_id=account_id, space_id=space_id)
    ):
        allowlist.extend([TOOL_FETCH_APPROVED_PAGE, TOOL_SEARCH_WEB])
    return sorted(allowlist)


def validate_input(spec: ToolSpec, payload: dict[str, Any]) -> None:
    """按注册表 schema 校验输入（子集校验器：object/string/integer/boolean）。"""
    _validate_value(spec.input_schema, payload, path="$")


def _deny(status_code: int, code: str, message: str, detail: dict[str, object] | None) -> NoReturn:
    raise ToolProtocolError(status_code, code, message, detail)


def _validate_value(schema: dict[str, Any], value: Any, *, path: str) -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "输入必须为 object", {"path": path})
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                _deny(
                    422,
                    "AGENT_TOOL_SCHEMA_INVALID",
                    "缺少必填字段",
                    {"path": f"{path}.{key}"},
                )
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                _deny(
                    422,
                    "AGENT_TOOL_SCHEMA_INVALID",
                    "存在未声明的额外字段",
                    {"path": path, "extra": extra},
                )
        for key, sub in properties.items():
            if key in value and isinstance(sub, dict) and "type" in sub:
                _validate_value(sub, value[key], path=f"{path}.{key}")
        return
    if expected not in _SUPPORTED_TYPES:
        # 注册表自身配置错误：服务器内部错误而非调用方问题
        _deny(500, "INTERNAL_ERROR", "工具 schema 类型不受支持", {"path": path})
    if expected == "string":
        if not isinstance(value, str):
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段须为 string", {"path": path})
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字符串超长", {"path": path})
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字符串过短", {"path": path})
        if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段值不在允许枚举中", {"path": path})
        return
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段须为 integer", {"path": path})
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段小于允许最小值", {"path": path})
        if isinstance(maximum, int | float) and value > maximum:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段超过允许最大值", {"path": path})
        if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段值不在允许枚举中", {"path": path})
        return
    if expected == "array":
        if not isinstance(value, list):
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段须为 array", {"path": path})
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "数组元素过少", {"path": path})
        if isinstance(max_items, int) and len(value) > max_items:
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "数组元素过多", {"path": path})
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item_schema, item, path=f"{path}[{index}]")
        return
    if not isinstance(value, bool):  # expected == "boolean"
        _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段须为 boolean", {"path": path})


def check_scope(run: AgentRun, claims: dict[str, Any], spec: ToolSpec) -> None:
    """allowlist + required_kind 双重 scope 门禁（拒绝由 execute() 统一审计）。"""
    allowlist = run.tool_allowlist_json or []
    if spec.name not in allowlist:
        raise ToolProtocolError(
            403,
            "AGENT_TOOL_SCOPE_DENIED",
            "工具不在该 Run 的 allowlist 内",
            {"tool": spec.name, "reason": "not_in_allowlist"},
        )
    if spec.required_kind is not None and claims.get("agent_kind") != spec.required_kind:
        raise ToolProtocolError(
            403,
            "AGENT_TOOL_SCOPE_DENIED",
            "当前 agent kind 无权调用该工具",
            {"tool": spec.name, "reason": "kind_scope"},
        )


@dataclass(frozen=True)
class ToolRunScope:
    """The admitted run identity, unaffected by ORM expiration after commit."""

    id: int
    job_id: int | None
    kind: str
    policy_version: str
    attempt: int
    tool_allowlist_json: tuple[str, ...]

    @classmethod
    def capture(cls, run: AgentRun) -> ToolRunScope:
        return cls(
            run.id,
            run.job_id,
            run.kind,
            run.policy_version,
            run.attempt,
            tuple(run.tool_allowlist_json or []),
        )


def _record_tool_failure(
    db: Session,
    *,
    run: AgentRun,
    name: str,
    version: int,
    tool_call_id: str | None,
    execution: ExecutionIdentity | StewardExecution | None,
    error_class: str,
) -> None:
    """把一次**非协议**的工具执行失败写成持久审计行。

    为什么不入 `agent_tool_calls`：那张表是副作用去重台账（同 (run_id, tool_call_id)
    至多一行，且失败调用没有副作用），而且它在准入 CAS **之后**才写入——CAS 自己
    抛异常时根本无法插入。因此失败事实进 `audit_log`，与既有的 `agent_tool_denied`
    同一条路径。

    本函数**不得**影响原异常的传播：写审计失败只记日志。detail 只放异常类型名，
    不放 message / SQL / 参数（可能含数据）。
    """
    try:
        db.rollback()
        steward_scope = execution if isinstance(execution, StewardExecution) else None
        detail: dict[str, object] = {
            "tool": name,
            "version": version,
            "error_class": error_class,
            "agent_kind": run.kind,
            "space_id": steward_scope.space_id if steward_scope is not None else None,
        }
        if tool_call_id is not None:
            detail["tool_call_id"] = tool_call_id
        audit.write_audit(
            db,
            action="agent_tool_failed",
            actor_id=None,
            target_id=run.id,
            detail=detail,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - 审计失败不得掩盖原异常
        logger.exception("failed to record tool failure audit tool=%s", name)
        db.rollback()


def execute(
    db: Session,
    run: AgentRun,
    agent_session: AgentSession | None,
    claims: dict[str, Any],
    *,
    name: str,
    version: int,
    input_payload: dict[str, Any],
    tool_call_id: str | None = None,
    execution: ExecutionIdentity | StewardExecution | None = None,
) -> dict[str, Any]:
    """running 态门禁 → 副作用去重 → 注册表校验 → scope 门禁 → schema 校验 → 执行 → 审计。

    五类协议拒绝在此统一写安全审计并先提交（审计不随拒绝回滚），再抛 API 错误。
    tool_call_id 为 sidecar 透传元数据：同 (run_id, tool_call_id) 幂等重放首次输出
    （不重执行、不重复审计）；不同工具/版本 → AGENT_TOOL_CALL_CONFLICT。
    """
    claim: AgentToolCall | None = None
    admitted: ToolRunScope | None = None
    claim_id: int | None = None
    try:
        if execution is not None:
            if isinstance(execution, ExecutionIdentity):
                run, fenced_session, _job = fence_assistant_execution(
                    db, execution, allowed_statuses=("running",)
                )
                agent_session = fenced_session
            else:
                run, _attempt, _steward_job = fence_steward_execution(
                    db, execution, allowed_statuses=("running",)
                )
        if run.status != "running":
            raise ToolProtocolError(
                409,
                "AGENT_RUN_NOT_RUNNING",
                "工具仅在 running 状态可执行",
                {"status": run.status},
            )
        # Cancellation is a server-side decision and can race with the
        # sidecar heartbeat.  Refresh immediately before any dedupe or dispatch
        # work so a cancelled run cannot start a new tool call while its FSM is
        # still in ``running``.
        # Do not roll back the caller's transaction here: the API may have
        # just promoted ``run.started`` in the same session.  A scalar refresh
        # preserves those pending FSM writes while obtaining the latest flag
        # visible to this transaction.
        # Expire only the mutable gate fields.  A rollback here would discard
        # the in-transaction ``run.started`` promotion that callers may have
        # just appended, so preserve the FSM write while forcing a fresh
        # scalar read for cancellation.
        db.expire(run, ("cancel_requested",))
        cancel_requested = run.cancel_requested
        if run.status != "running":
            raise ToolProtocolError(
                409,
                "AGENT_RUN_NOT_RUNNING",
                "工具仅在 running 状态可执行",
                {"status": run.status},
            )
        if bool(cancel_requested):
            raise ToolProtocolError(
                409,
                "AGENT_RUN_NOT_RUNNING",
                "Run 已请求取消，拒绝新的工具调用",
                {"reason": "cancel_requested"},
            )

        # 副作用去重先于任何执行：同 id 命中直接返回首次结果（调用方/端点照常运行
        # tool_result_hook，不绕过结果策略）。result_json 为空表示占位仍在途，
        # 说明另一并发请求正在执行同 tool_call_id（P1 并发去重窗口收口）。
        def _prior() -> AgentToolCall | None:
            if tool_call_id is None:
                return None
            return db.scalar(
                select(AgentToolCall).where(
                    AgentToolCall.run_id == run.id,
                    AgentToolCall.tool_call_id == tool_call_id,
                )
            )

        def _replay_or_reject(prior: AgentToolCall) -> dict[str, Any] | None:
            if prior.tool_name != name or prior.tool_version != version:
                raise_api_error(
                    409,
                    AGENT_TOOL_CALL_CONFLICT,
                    "相同 tool_call_id 但工具或版本不一致",
                    detail={
                        "tool_call_id": tool_call_id,
                        "expected": {"tool": prior.tool_name, "version": prior.tool_version},
                        "got": {"tool": name, "version": version},
                    },
                )
            if not prior.result_json:
                raise_api_error(
                    409,
                    AGENT_TOOL_CALL_IN_PROGRESS,
                    "相同 tool_call_id 的调用正在并发执行",
                    detail={"tool_call_id": tool_call_id},
                )
            return dict(prior.result_json)

        prior = _prior()
        if prior is not None:
            replayed = _replay_or_reject(prior)
            if replayed is not None:
                return replayed
        spec = resolve_tool(name, version)
        check_scope(run, claims, spec)
        # Keep the relationship-intelligence feature flag as the first
        # capability gate.  This preserves the public contract that disabled
        # kinship tools uniformly return KINSHIP_FLAG_DISABLED, even when a
        # caller omitted a newly-added consent field.  Schema validation still
        # runs before any enabled tool can execute.
        if spec.name in _KINSHIP_INTAKE_TOOLS and not config.RELATIONSHIP_INTELLIGENCE_ENABLED:
            raise ToolProtocolError(503, KINSHIP_FLAG_DISABLED, "关系智能能力未启用")
        validate_input(spec, input_payload)
        # Atomically admit dispatch after all validation.  A compare-and-set
        # UPDATE acquires SQLite's write lock, so cancellation either commits
        # first (rowcount=0 and this call is rejected) or waits until admission
        # commits (the call is then already in flight and may finish normally).
        # Flush an in-request ``run.started`` promotion before the textual
        # compare-and-set; otherwise the database would still see ``leased``
        # while the ORM object already reports ``running``.
        db.flush()
        admission = cast(
            CursorResult[Any],
            db.execute(
                text(
                    # `cancel_requested` 在 PostgreSQL 上是 boolean，拿它和整数比会得到
                    # `UndefinedFunction: operator does not exist: boolean = integer`——
                    # 而 SQLite 无严格类型（存 0/1），所以旧写法通过了全部 SQLite 测试，
                    # 切到 PG 当天就让**每一次工具调用**500（生产实测 2324 次/72h）。
                    # `FALSE` 字面量两方言都正确（SQLite ≥3.23 支持 TRUE/FALSE）。
                    "UPDATE agent_runs SET updated_at = updated_at "
                    "WHERE id = :run_id AND status = 'running' AND cancel_requested = FALSE"
                ),
                {"run_id": run.id},
            ),
        )
        if admission.rowcount != 1:
            db.rollback()
            raise ToolProtocolError(
                409,
                "AGENT_RUN_NOT_RUNNING",
                "Run 已停止或请求取消，拒绝新的工具调用",
                {"reason": "cancel_requested"},
            )
        # 原子占位：唯一索引 (run_id, tool_call_id) 使并发同 id 请求在 flush 处
        # 冲突，先占位者独占执行权，后到者回放/拒绝——副作用不再可能双执行。
        if tool_call_id is not None:
            claim = AgentToolCall(
                run_id=run.id,
                tool_call_id=tool_call_id,
                tool_name=spec.name,
                tool_version=spec.version,
                result_json={},
                created_at=timeutil.utcnow(),
            )
            db.add(claim)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                prior = _prior()
                if prior is None:  # pragma: no cover - 理论不可达：占位已被提交才可能冲突
                    raise
                replayed = _replay_or_reject(prior)
                if replayed is not None:
                    return replayed
                raise  # pragma: no cover
        # Admission and dedupe reservation commit together before any network
        # tool can run. The immutable scope remains the *admitted* attempt even
        # if reaper/lease advances the Run after this commit. An already admitted
        # invocation may finish; a new invocation must pass the current fence.
        admitted = ToolRunScope.capture(run)
        claim_id = claim.id if claim is not None else None
        db.commit()
        # 分发也纳入同一拒绝审计路径：领域工具的范围/形状拒绝同属协议违规
        output = _dispatch(
            db,
            spec,
            run=admitted,
            agent_session=agent_session,
            execution=execution,
            input_payload=input_payload,
        )
        # Pass the same JSON value through the result policy on first execution
        # and replay; otherwise datetime fields bypass that policy only once.
        output = cast(dict[str, Any], jsonable_encoder(output))
        if claim is not None:
            # Store the same JSON representation the HTTP response emits;
            # gateway results contain expiry datetimes, which bare SQL JSON
            # cannot serialize and would strand the admitted reservation.
            claim.result_json = dict(output)
    except ToolProtocolError as exc:
        if admitted is not None:
            db.rollback()
        if claim_id is not None:
            # A known refusal can release this invocation's empty reservation;
            # never delete another invocation or a completed result. Unknown
            # process death remains in-progress (fail closed against repeats).
            db.execute(
                delete(AgentToolCall).where(
                    AgentToolCall.id == claim_id,
                    AgentToolCall.result_json == {},
                )
            )
        audit.write_audit(
            db,
            action="agent_tool_denied",
            actor_id=None,
            target_id=run.id,
            detail={"tool": name, "reason": exc.code},
        )
        db.commit()
        raise_api_error(exc.status_code, exc.code, exc.message, exc.detail)
    except HTTPException:
        # `raise_api_error` 就是以 HTTPException 传递**全部**领域错误（404
        # FG_PROFILE_NOT_AVAILABLE、409 去重冲突、403 scope 不匹配……）。它们已经
        # 类型化且有错误码，必须原样透传——归入下面的兑底分支会把每一条领域错误
        # 都变成通用 500（实测：这是引入兑底时踩到的回归）。
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底：任何非协议异常都必须留痕
        # 未处理异常过去直接逃逸到 FastAPI 的 500 处理：模型只收到通用
        # `INTERNAL_ERROR`，`agent_tool_calls` 与审计都**零行**，于是「所有工具调用
        # 在 PG 上恒失败」静默潜伏了数天（生产 2324 次/72h）。这里把两件事补齐：
        # 记下失败事实（可在库里查到），并给模型一个可辨认的错误码。
        _record_tool_failure(
            db,
            run=run,
            name=name,
            version=version,
            tool_call_id=tool_call_id,
            execution=execution,
            error_class=type(exc).__name__,
        )
        # 不记录异常 message / SQL / 参数：它们可能含数据，只留异常类型名。
        logger.exception(
            "tool execution failed tool=%s version=%s error_class=%s",
            name,
            version,
            type(exc).__name__,
        )
        # 500 而非 503：sidecar 只把 502/503/504 视为 transient，而这类失败是代码缺陷，
        # 重试无意义；用专属错误码让模型与日志能区分「工具坏了」与「上游暂时不可用」。
        raise_api_error(
            500,
            "AGENT_TOOL_EXECUTION_FAILED",
            "工具执行失败",
            {"error_class": type(exc).__name__, "tool": name},
        )
    audit_detail: dict[str, object] = {
        "tool": spec.name,
        "version": spec.version,
        "attempt": admitted.attempt if admitted is not None else run.attempt,
    }
    if tool_call_id is not None:
        audit_detail["tool_call_id"] = tool_call_id
    # Assistant audits carry account/session ownership; Steward audits carry the
    # verified space/viewer scope and deliberately have no AgentSession.
    account = db.get(Account, agent_session.account_id) if agent_session is not None else None
    steward_scope = execution if isinstance(execution, StewardExecution) else None
    audit_detail.update(
        {
            "agent_kind": run.kind,
            "space_id": steward_scope.space_id if steward_scope is not None else None,
            "viewer_account_id": (
                steward_scope.viewer_account_id if steward_scope is not None else None
            ),
        }
    )
    if agent_session is not None:
        audit_detail.update(
            {"account_id": agent_session.account_id, "session_id": agent_session.id}
        )
    audit.write_audit(
        db,
        action="agent_tool_executed",
        actor_id=account.user_id if account is not None else None,
        target_id=run.id,
        detail=audit_detail,
    )
    return output


logger = logging.getLogger(__name__)

#: `propose_memory` 的 extractor_version：与规则提取器区分开，使审计能分辨
#: 「模型提议」与「settle 规则提取」两个来源。
MEMORY_TOOL_EXTRACTOR_VERSION = "assistant-tool-v1"


def _search_memory_tool(
    db: Session, *, actor: User, space_id: int, query: str, limit: int
) -> dict[str, Any]:
    """只读检索：复用 `search_rag`，**不新增检索路径**。

    与 `search_rag` 共用同一 eligibility 过滤与 `_rows_to_hits` 引用投影，
    因此授权等价性不是靠约定，而是靠同一段代码。输出只含句柄与元数据，
    不含原始来源的敏感字段。
    """
    from app.services import memory_rag

    clean = query.strip()
    if not clean:
        raise ToolProtocolError(
            422, "AGENT_TOOL_SCHEMA_INVALID", "查询不能为空", {"path": "$.query"}
        )
    try:
        hits = memory_rag.search_rag(
            db,
            actor=actor,
            account=actor.account,
            space_id=space_id,
            query=clean,
            agent_kind="assistant",
            limit=limit,
            for_model=True,
        )
    except HTTPException as exc:
        # 记忆/检索未启用或来源受限：按既有 API 口径对外，不伪装成空结果。
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "MEMORY_SEARCH_FAILED")
        raise ToolProtocolError(
            exc.status_code, code, str(detail.get("message") or "检索不可用")
        ) from exc
    return {
        "query_hash": memory_rag.query_hash(clean),
        "results": [
            {
                "citation": hit.citation_handle,
                "source_type": hit.source_type,
                "source_id": hit.source_id,
                "scope": hit.scope,
                "sensitivity": hit.sensitivity,
                "revision": hit.revision,
                "excerpt": hit.text[:400],
            }
            for hit in hits
        ],
    }


def _propose_memory_tool(
    db: Session, *, actor: User, run: ToolRunScope, summary: str, purpose: str | None
) -> dict[str, Any]:
    """提议一条 **pending** 记忆候选。

    三条刻意的约束：

    1. `source_quote` 取本轮原始 user 消息**全文**，不是模型的转述——
       `memory_sources` 对 `agent_message` 来源做全等校验，用转述会 422；而且
       「哪句原话」是审计真源，模型不该改写它。
    2. `suggested_scope` 固定 `private`：模型不得替用户选择共享范围，
       最小披露是安全默认。
    3. **只创建候选**，不调用 `confirm_candidate`。确认是用户对「这条事实进入我的
       记忆」的明示同意，属于产品语义，不是技术细节。
    """
    from app.models.agent import AgentMessage, AgentRun
    from app.services import memory_rag

    text = summary.strip()
    if not text:
        raise ToolProtocolError(
            422, "AGENT_TOOL_SCHEMA_INVALID", "摘要不能为空", {"path": "$.summary"}
        )
    source = db.get(AgentRun, run.id, populate_existing=True)
    if source is None or source.message_id is None:  # pragma: no cover - 执行门禁保证
        raise ToolProtocolError(409, "AGENT_TOOL_RUN_INVALID", "本轮 run 没有可引用的用户消息")
    message = db.get(AgentMessage, source.message_id, populate_existing=True)
    if message is None or message.role != "user":
        raise ToolProtocolError(409, "AGENT_TOOL_RUN_INVALID", "本轮 run 没有可引用的用户消息")
    raw = message.content_json.get("text")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolProtocolError(409, "AGENT_TOOL_RUN_INVALID", "本轮用户消息为空")
    try:
        candidate = memory_rag.propose_candidate(
            db,
            author_account_id=actor.account.id,
            source={"kind": "agent_message", "message_id": message.id},
            source_quote=raw,
            summary=text,
            suggested_scope="private",
            purpose=(purpose or "用户在对话中明确要求记住").strip()[:120],
            extractor_version=MEMORY_TOOL_EXTRACTOR_VERSION,
        )
    except HTTPException as exc:
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "MEMORY_PROPOSE_FAILED")
        raise ToolProtocolError(
            exc.status_code, code, str(detail.get("message") or "提议失败")
        ) from exc
    db.flush()
    return {
        "candidate_id": candidate.id,
        "status": candidate.status,
        "suggested_scope": candidate.suggested_scope,
        "requires_user_confirmation": True,
    }


def _dispatch(
    db: Session,
    spec: ToolSpec,
    *,
    run: ToolRunScope,
    agent_session: AgentSession | None,
    execution: ExecutionIdentity | StewardExecution | None,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """分发执行：V2.2 只读领域工具走 AgentQueryService；骨架工具无副作用。

    领域服务的 QueryToolError 在此转译为 ToolProtocolError，使范围/形状拒绝
    复用 execute() 的统一安全审计路径；正常业务结果错误（如档案不可见）不
    属协议违规，由服务层直接抛统一 API 错误。
    V2.3 E4a：Relationship Intelligence 工具在 flag 关闭时一律拒绝（503，
    与浏览器面同一口径），拒绝走统一安全审计。
    """
    if isinstance(execution, StewardExecution):
        if spec.name not in steward_tools.STEWARD_TOOL_NAMES:
            raise ToolProtocolError(
                403,
                "AGENT_TOOL_SCOPE_DENIED",
                "Steward 只能调用只读工具",
                {"tool": spec.name},
            )
        if (
            spec.name in steward_tools.STEWARD_VIEWER_TOOL_NAMES
            and execution.viewer_account_id is None
        ):
            raise ToolProtocolError(
                403,
                "STEWARD_VIEWER_SCOPE_UNAVAILABLE",
                "该 Steward attempt 没有 viewer scope",
                {"tool": spec.name},
            )
        return agent_query.enforce_output_limit(
            steward_tools.execute_steward_tool(
                db,
                execution=execution,
                name=spec.name,
                input_payload=input_payload,
            )
        )
    if agent_session is None:
        raise ToolProtocolError(500, "INTERNAL_ERROR", "Assistant scope 缺少 session")
    if spec.name == TOOL_SEARCH_MEMORY:
        actor, space = agent_query._resolve_scope(db, agent_session)
        limit = input_payload.get("limit")
        if limit is not None and (isinstance(limit, bool) or not 1 <= limit <= 20):
            raise ToolProtocolError(
                422,
                "AGENT_TOOL_SCHEMA_INVALID",
                "字段超出允许范围",
                {"path": "$.limit", "allowed": "1..20"},
            )
        return _search_memory_tool(
            db,
            actor=actor,
            space_id=space.id,
            query=input_payload["query"],
            limit=5 if limit is None else int(limit),
        )
    if spec.name == TOOL_PROPOSE_MEMORY:
        actor, space = agent_query._resolve_scope(db, agent_session)
        del space
        return _propose_memory_tool(
            db,
            actor=actor,
            run=run,
            summary=input_payload["summary"],
            purpose=input_payload.get("purpose"),
        )
    if spec.name == TOOL_RESOLVE_FREE_TEXT_RELATION:
        actor, space = agent_query._resolve_scope(db, agent_session)
        return intake_extractor.parse_free_text_relation(
            db,
            account_id=actor.account.id,
            user_id=actor.id,
            space_id=space.id,
            text=input_payload["text"],
            surface=intake_extractor.SURFACE_ASSISTANT,
        )
    if spec.name == TOOL_GET_TERM_ALTERNATIVES:
        actor, space = agent_query._resolve_scope(db, agent_session)
        limit = input_payload.get("limit")
        if limit is not None and (isinstance(limit, bool) or not 1 <= limit <= 10):
            raise ToolProtocolError(
                422,
                "AGENT_TOOL_SCHEMA_INVALID",
                "字段超出允许范围",
                {"path": "$.limit", "allowed": "1..10"},
            )
        return terms.list_term_alternatives(
            db,
            account_id=actor.account.id,
            space_id=space.id,
            concept_code=input_payload["concept_code"],
            limit=5 if limit is None else int(limit),
        )
    if spec.name == TOOL_RECORD_TERM_USAGE:
        if (
            input_payload.get("consent_confirmed") is not True
            or not agent_session.term_usage_consent
        ):
            raise ToolProtocolError(
                403,
                "AGENT_TERM_USAGE_CONSENT_REQUIRED",
                "记录称谓使用前需要用户明确确认",
                {"tool": spec.name},
            )
        actor, space = agent_query._resolve_scope(db, agent_session)
        _usage, created, summary = terms.record_usage_and_promote(
            db,
            space_id=space.id,
            concept_code=input_payload["concept_code"],
            term=input_payload["term"],
            account_id=actor.account.id,
            profile_id=actor.id,
            source_event="assistant_query",
        )
        return {
            "recorded": created,
            "promotion": {
                "promoted": bool(summary["promoted"]),
                "demoted": bool(summary["demoted"]),
                "eligible_accounts": int(summary["eligible_accounts"]),
            },
        }
    if spec.name == TOOL_SEARCH_WEB:
        try:
            result = controlled_web.search_web(
                db,
                account_id=agent_session.account_id,
                space_id=agent_session.space_id,
                run_id=run.id,
                query=input_payload["query"],
                use_case=input_payload["use_case"],
                limit=int(input_payload.get("limit", 5)),
            )
        except controlled_web.WebGatewayError as exc:
            raise ToolProtocolError(exc.status_code, exc.code, exc.message, exc.detail) from None
        return result
    if spec.name == TOOL_FETCH_APPROVED_PAGE:
        try:
            result = controlled_web.fetch_approved_page(
                db,
                account_id=agent_session.account_id,
                space_id=agent_session.space_id,
                run_id=run.id,
                approved_token=input_payload["approved_token"],
            )
        except controlled_web.WebGatewayError as exc:
            raise ToolProtocolError(exc.status_code, exc.code, exc.message, exc.detail) from None
        return result
    if spec.name in agent_query.QUERY_TOOL_NAMES:
        try:
            return agent_query.execute_query_tool(
                db,
                agent_session=agent_session,
                name=spec.name,
                input_payload=input_payload,
            )
        except agent_query.QueryToolError as exc:
            raise ToolProtocolError(exc.status_code, exc.code, exc.message, exc.detail) from None
    if spec.name == TOOL_ECHO:
        return {"text": input_payload["text"]}
    if spec.name == TOOL_PROBE_SCOPE:
        return {
            "run_id": run.id,
            "job_id": run.job_id,
            "agent_kind": run.kind,
            "account_id": agent_session.account_id,
            "space_id": agent_session.space_id,
            "policy_version": run.policy_version,
            "tool_allowlist": list(run.tool_allowlist_json),
            "attempt": run.attempt,
        }
    raise ToolProtocolError(404, "AGENT_TOOL_UNKNOWN", "未知工具", {"tool": spec.name})
