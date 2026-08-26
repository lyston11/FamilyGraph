"""版本化领域工具注册表与执行门禁（RT-3 / notes.md）。

骨架协议工具（echo、probe_scope、steward_ping）验证「token scope → 注册表
校验 → 服务层执行 → 审计」全链路；V2.2 起六个只读领域工具（AgentQueryService，
见 services/agent_query.py）以 min_kind=assistant 注册。严格校验 fail-closed，
四类拒绝码均写安全审计：
- 未知工具        → AGENT_TOOL_UNKNOWN
- 版本不匹配      → AGENT_TOOL_VERSION_UNSUPPORTED
- schema 违规     → AGENT_TOOL_SCHEMA_INVALID（额外字段/类型/必填）
- allowlist/kind  → AGENT_TOOL_SCOPE_DENIED

执行本身也留审计（只记工具名/版本/run/attempt，不记输入输出 payload）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from app import config
from app.errors import KINSHIP_FLAG_DISABLED, raise_api_error
from app.models.agent import AgentRun, AgentSession
from app.services import agent_query, audit, intake_extractor, terms

# JSON schema 子集校验支持的标量类型
_SUPPORTED_TYPES = {"string", "integer", "boolean"}


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
    min_kind: str | None = None  # None = assistant/steward 均可用
    # 兼容旧调用方仍可请求的版本集合；None = 仅当前 version。
    # V2.3：两个关系工具升 @2（Relationship Intelligence 解析），保留 @1 声明，
    # sidecar 未跟进升级前继续以 @1 调用（E4 才切换）。
    supported_versions: tuple[int, ...] | None = None


TOOL_ECHO = "familygraph.echo"
TOOL_PROBE_SCOPE = "familygraph.probe_scope"
TOOL_STEWARD_PING = "familygraph.steward_ping"

# V2.3 Block E4a：Relationship Intelligence 内部工具（min_kind=assistant，@1）。
# RELATIONSHIP_INTELLIGENCE_ENABLED 关闭时一律拒绝（与浏览器面 503 同一口径）。
TOOL_RESOLVE_FREE_TEXT_RELATION = "familygraph.resolve_free_text_relation"
TOOL_GET_TERM_ALTERNATIVES = "familygraph.get_term_alternatives"
TOOL_RECORD_TERM_USAGE = "familygraph.record_term_usage"
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
    """六个版本化只读领域工具的注册表条目（min_kind=assistant）。"""
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
                min_kind="assistant",
                supported_versions=versions,
            )
        )
    return tuple(specs)


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        *_query_tool_specs(),
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
        ),
        ToolSpec(
            name=TOOL_STEWARD_PING,
            version=1,
            description="steward 专用探针（演示 min_kind scope 门禁）",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            output_schema={"type": "object", "properties": {}},
            min_kind="steward",
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
            min_kind="assistant",
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
            min_kind="assistant",
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
                },
                "required": ["concept_code", "term"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            min_kind="assistant",
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


def default_allowlist(kind: str) -> list[str]:
    """浏览器创建 Run 的默认工具白名单：注册表内该 kind 可用的全部骨架/领域工具。

    min_kind='steward' 的工具不对 assistant 开放（与执行期门禁同一语义）；
    V2.2 只读领域工具自动纳入 assistant 默认白名单（仍零写入）。
    """
    return sorted(
        name for name, spec in REGISTRY.items() if spec.min_kind is None or spec.min_kind == kind
    )


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
        return
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段须为 integer", {"path": path})
        return
    if not isinstance(value, bool):  # expected == "boolean"
        _deny(422, "AGENT_TOOL_SCHEMA_INVALID", "字段须为 boolean", {"path": path})


def check_scope(run: AgentRun, claims: dict[str, Any], spec: ToolSpec) -> None:
    """allowlist + min_kind 双重 scope 门禁（拒绝由 execute() 统一审计）。"""
    allowlist = run.tool_allowlist_json or []
    if spec.name not in allowlist:
        raise ToolProtocolError(
            403,
            "AGENT_TOOL_SCOPE_DENIED",
            "工具不在该 Run 的 allowlist 内",
            {"tool": spec.name, "reason": "not_in_allowlist"},
        )
    if spec.min_kind is not None and claims.get("agent_kind") != spec.min_kind:
        raise ToolProtocolError(
            403,
            "AGENT_TOOL_SCOPE_DENIED",
            "当前 agent kind 无权调用该工具",
            {"tool": spec.name, "reason": "kind_scope"},
        )


def execute(
    db: Session,
    run: AgentRun,
    agent_session: AgentSession,
    claims: dict[str, Any],
    *,
    name: str,
    version: int,
    input_payload: dict[str, Any],
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    """running 态门禁 → 注册表校验 → scope 门禁 → schema 校验 → 执行 → 审计。

    五类协议拒绝在此统一写安全审计并先提交（审计不随拒绝回滚），再抛 API 错误。
    tool_call_id 为 sidecar 透传元数据，仅记录进执行审计（副作用去重表 V2.4 落地）。
    """
    try:
        if run.status != "running":
            raise ToolProtocolError(
                409,
                "AGENT_RUN_NOT_RUNNING",
                "工具仅在 running 状态可执行",
                {"status": run.status},
            )
        spec = resolve_tool(name, version)
        check_scope(run, claims, spec)
        validate_input(spec, input_payload)
        # 分发也纳入同一拒绝审计路径：领域工具的范围/形状拒绝同属协议违规
        output = _dispatch(
            db, spec, run=run, agent_session=agent_session, input_payload=input_payload
        )
    except ToolProtocolError as exc:
        audit.write_audit(
            db,
            action="agent_tool_denied",
            actor_id=None,
            target_id=run.id,
            detail={"tool": name, "reason": exc.code},
        )
        db.commit()
        raise_api_error(exc.status_code, exc.code, exc.message, exc.detail)
    audit_detail: dict[str, object] = {
        "tool": spec.name,
        "version": spec.version,
        "attempt": run.attempt,
    }
    if tool_call_id is not None:
        audit_detail["tool_call_id"] = tool_call_id
    audit.write_audit(
        db,
        action="agent_tool_executed",
        actor_id=agent_session.account_id,
        target_id=run.id,
        detail=audit_detail,
    )
    return output


def _dispatch(
    db: Session,
    spec: ToolSpec,
    *,
    run: AgentRun,
    agent_session: AgentSession,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """分发执行：V2.2 只读领域工具走 AgentQueryService；骨架工具无副作用。

    领域服务的 QueryToolError 在此转译为 ToolProtocolError，使范围/形状拒绝
    复用 execute() 的统一安全审计路径；正常业务结果错误（如档案不可见）不
    属协议违规，由服务层直接抛统一 API 错误。
    V2.3 E4a：Relationship Intelligence 工具在 flag 关闭时一律拒绝（503，
    与浏览器面同一口径），拒绝走统一安全审计。
    """
    if spec.name in _KINSHIP_INTAKE_TOOLS and not config.RELATIONSHIP_INTELLIGENCE_ENABLED:
        raise ToolProtocolError(503, KINSHIP_FLAG_DISABLED, "关系智能能力未启用")
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
    if spec.name == TOOL_STEWARD_PING:
        return {"ok": True, "space_id": agent_session.space_id}
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
