"""Steward 模型辅助出站安全链（09-11 quality-security；F07/F08/F18）。

安全链（design.md）：
`Steward input projection → context/input policy → provider setting/revision →
before_provider_request → bounded transport → typed output validator →
evidence/permission fence → presentation/review projection`。

本模块只承载纯函数与投影上下文（不发起网络、不做写事务）：

- **R1 受众限定输入投影**：候选/排序/解释的 prompt 输入一律使用本次稳定的节点
  代号（``n001..``）+ 已确认 fact type/id/revision + minor 布尔标记；绝不把
  ``User.name`` 或其他原始字段当可外发数据。真实展示名只在服务端按 recipient
  当前权限替换（解释渲染时）。space-wide visible 集合只决定投影范围，不是
  每个账号的授权集合（卡片授权仍由确定性矩阵/收件人判定）。
- **R1 出站最终检查**：``outbound_check`` 直接复用 ``policy_guard.
  before_provider_request``（不得经 run-bound ProviderProxy 绕过最终 payload
  检查，也不伪造 AgentRun）。云同意撤销或本地不可用按策略降级，绝不自动切云。
- **R2 封闭输出 schema**：类型化校验器。解释输出必须是
  ``{reason_code, supporting_fact_ids, template_slots}``，所有字段/ID 必须存在于
  本次输入证据内，再由确定性模板渲染；候选 kind 只允许原子 SOURCE_FACT_TYPES
  （祖辈/称谓等派生概念不能被模型写成父母事实）；排序必须严格排列。
- **R3 候选资格与最小化**：候选是线索，校验器输出不含模型自由文本（rationale
  丢弃，digest 基于结构/证据）；候选输出形状对齐 candidate-review：
  ``{"kind", "subject_user_id", "object_user_id"}``（下一任务构建审核循环时
  直接消费该映射）。

明文（姓名/注入句/token）只在本进程内存的原始 User 行里，从不进入 payload、
审计或落库字段。
"""

# Note: 模型输入投影与输出校验唯一入口 —
# 见 .agent-notes/implemented/bug-fix/2026-09-11-steward-guard-closed-schema-and-minimal-input.md

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.relationship_facts import SOURCE_FACT_TYPES
from app.services import policy_guard
from app.services.policy_guard import PolicyDecision

# ---- 解释输出封闭 schema（R2/R5：schema 版本；旧纯文本行 = untrusted）----
EXPLANATION_SCHEMA_VERSION = 2

# relation_proposal 只指向原子 SOURCE_FACT_TYPES；祖辈/称谓等派生概念禁止
CANDIDATE_ATOMIC_KINDS: tuple[str, ...] = SOURCE_FACT_TYPES

# 解释 reason_code 封闭集合（按卡片 kind 映射；未知 kind 一律拒绝）
EXPLANATION_REASON_CODES: dict[str, str] = {
    "household_link": "household_link_available",
    "lineage_request": "lineage_request_available",
}

# 各 reason_code 允许的 template_slots（值必须是本次输入内的节点代号）
_EXPLANATION_SLOTS: dict[str, tuple[str, ...]] = {
    "household_link_available": ("partner_node",),
    "lineage_request_available": ("relative_node",),
}

_NODE_CODENAME_RE = re.compile(r"^n\d{3,}$")

# 候选输出形状（candidate-review 消费合同；下一任务构建审核循环）
CANDIDATE_PAYLOAD_KEYS = ("kind", "subject_user_id", "object_user_id")


class ProjectionContext:
    """一次辅助批次的输入投影上下文（仅内存；绝不落库/入日志）。

    codename 分配对排序后的 visible 用户 id 稳定（n001..），同一批次内可复现，
    校验阶段据此把模型输出的代号映射回 user id。
    """

    def __init__(self, visible: set[int], *, minor_ids: set[int] | None = None) -> None:
        self._ids = sorted(int(i) for i in visible)
        self.codename_by_user: dict[int, str] = {
            uid: f"n{index + 1:03d}" for index, uid in enumerate(self._ids)
        }
        self.user_by_codename: dict[str, int] = {
            codename: uid for uid, codename in self.codename_by_user.items()
        }
        self.minor_ids: set[int] = {int(i) for i in (minor_ids or set())}

    def codename(self, user_id: int) -> str | None:
        return self.codename_by_user.get(int(user_id))

    def user_id(self, codename: str) -> int | None:
        return self.user_by_codename.get(codename)

    @property
    def user_ids(self) -> list[int]:
        return list(self._ids)


# ---- R1：输入投影（scope/字段过滤先行；结构化 prompt；policy_guard 兜底）----


def project_candidate_input(facts: list[dict[str, Any]], ctx: ProjectionContext) -> str:
    """候选 prompt 输入：节点代号花名册（含 minor 标记）+ 已确认事实白名单。

    绝不含 display name、masked 原值、健康/住址等高敏感类别或私人内容。
    """
    nodes = [
        {"node": ctx.codename_by_user[uid], "minor": uid in ctx.minor_ids} for uid in ctx.user_ids
    ]
    projected_facts: list[dict[str, Any]] = []
    for fact in facts:
        subject_code = ctx.codename(int(fact["subject_user_id"]))
        object_raw = fact.get("object_user_id")
        object_code = ctx.codename(int(object_raw)) if object_raw is not None else None
        if subject_code is None or (object_raw is not None and object_code is None):
            continue  # 越出本次授权输入的端点：不投影
        projected_facts.append(
            {
                "fact_id": int(fact["fact_id"]),
                "fact_type": str(fact["fact_type"]),
                "revision": int(fact["revision"]),
                "subject": subject_code,
                "object": object_code,
            }
        )
    return json.dumps({"nodes": nodes, "facts": projected_facts}, ensure_ascii=False)


def project_ranking_input(cards: list[dict[str, Any]]) -> str:
    """排序 prompt 输入：card_id + kind（reason 文案不下发，避免携带展示名）。"""
    return json.dumps(
        [{"card_id": int(c["card_id"]), "kind": str(c["kind"])} for c in cards],
        ensure_ascii=False,
    )


def project_explanation_input(card: dict[str, Any], ctx: ProjectionContext) -> str:
    """解释 prompt 输入：卡片结构化信息 + 证据 fact id/type/revision + 节点代号。

    不下发 reason_text 模板文案原文（其中可能含展示名）；允许输出的 reason_code
    与 slot 键一并声明（封闭 schema 自描述）。
    """
    subject_code = ctx.codename(int(card["subject_user_id"]))
    object_code = ctx.codename(int(card["object_user_id"])) if card.get("object_user_id") else None
    reason_code = EXPLANATION_REASON_CODES.get(str(card["kind"]))
    return json.dumps(
        {
            "card_id": int(card["card_id"]),
            "kind": str(card["kind"]),
            "subject": subject_code,
            "object": object_code,
            "evidence_facts": [
                {"id": int(f["id"]), "type": str(f["type"]), "revision": int(f["revision"])}
                for f in card.get("evidence_facts", [])
            ],
            "allowed_reason_code": reason_code,
            "allowed_slot_keys": list(_EXPLANATION_SLOTS.get(reason_code or "", ())),
        },
        ensure_ascii=False,
    )


# ---- R1：出站最终 payload 检查（复用 policy_guard；绝不绕过）----


def outbound_check(
    payload: Any,
    *,
    provider_kind: str | None,
    local_required: bool,
    cloud_allowed: bool,
) -> PolicyDecision:
    """发送前最终 payload 检查；block/redact 语义与 policy_guard 一致。

    - local_required 且非本地 provider → block（本地不可用即降级，绝不自动切云）；
    - 云同意撤销（cloud_allowed=False）且非本地 → block；
    - 注入/秘密由 policy_guard 模式检测。
    """
    return policy_guard.before_provider_request(
        payload,
        provider_kind=provider_kind,
        local_required=local_required,
        cloud_allowed=cloud_allowed,
    )


# ---- R2：类型化输出校验器（封闭 schema；失败一律整体拒绝，绝不截断）----


def _extract_json(text: str) -> Any | None:
    """从模型文本提取 JSON（容忍 ```json 围栏）；失败返回 None。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        return json.loads(stripped.strip())
    except json.JSONDecodeError:
        start, end = stripped.find("["), stripped.rfind("]")
        if start < 0 or end <= start:
            start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None


def _valid_int(value: Any) -> int | None:
    """严格整数（拒绝 bool/浮点/字符串）；不合法返回 None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def validate_candidate_output(text: str, ctx: ProjectionContext) -> list[dict[str, Any]] | None:
    """校验候选输出：JSON 数组，每项 {kind, subject, object}（节点代号）。

    - kind 必须是原子 SOURCE_FACT_TYPES（派生称谓/祖辈一律丢弃）；
    - subject/object 必须是本次输入内的节点代号且互不相同；
    - 任一端点是未成年人 → 丢弃（未成年不进入推荐线索）；
    - rationale 等自由文本字段一律丢弃（不进 digest、不落库）。
    返回 candidate-review 消费形状列表（非空），整体不可解析 → None。
    """
    parsed = _extract_json(text)
    if not isinstance(parsed, list):
        return None
    items: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        cand_kind = entry.get("kind")
        subject_code, object_code = entry.get("subject"), entry.get("object")
        if not isinstance(cand_kind, str) or cand_kind not in CANDIDATE_ATOMIC_KINDS:
            continue
        if not isinstance(subject_code, str) or not isinstance(object_code, str):
            continue
        if not _NODE_CODENAME_RE.fullmatch(subject_code) or not _NODE_CODENAME_RE.fullmatch(
            object_code
        ):
            continue
        subject_id = ctx.user_id(subject_code)
        object_id = ctx.user_id(object_code)
        if subject_id is None or object_id is None or subject_id == object_id:
            continue  # 不在本次授权输入内（隐藏人物/编造 id）
        if subject_id in ctx.minor_ids or object_id in ctx.minor_ids:
            continue  # 未成年人不进入候选线索
        items.append(
            {
                "kind": cand_kind,
                "subject_user_id": subject_id,
                "object_user_id": object_id,
            }
        )
    return items or None


def validate_ranking_output(text: str, card_ids: list[int]) -> list[int] | None:
    """校验排序输出：严格排列（等长、无重复、集合相等、全为严格整数）。

    bool id、重复/遗漏 id、混入其他集合的 id 一律整体拒绝（None），绝不部分采纳。
    """
    parsed = _extract_json(text)
    if not isinstance(parsed, list):
        return None
    ints: list[int] = []
    for value in parsed:
        valid = _valid_int(value)
        if valid is None:
            return None
        ints.append(valid)
    if len(ints) != len(card_ids) or set(ints) != set(card_ids) or len(set(ints)) != len(ints):
        return None
    return ints


def validate_explanation_output(
    text: str,
    *,
    reason_code: str | None,
    evidence_fact_ids: list[int],
    slot_values_allowed: set[str],
) -> dict[str, Any] | None:
    """校验解释输出为封闭 schema：{reason_code, supporting_fact_ids, template_slots}。

    - reason_code 必须等于本次输入声明的允许值；
    - supporting_fact_ids 必须非空、无重复、全部存在于本次证据 fact id 内
      （编造人物/事实/路径 → 整体拒绝，绝不截断通过）；
    - template_slots 键必须属于该 reason_code 的封闭键集，值必须是本次输入内
      的节点代号（自由文本/超长文本/承诺句一律拒绝）。

    返回结构化 dict（不含渲染文本）；非法返回 None（回退模板）。
    """
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        return None
    if reason_code is None or parsed.get("reason_code") != reason_code:
        return None
    allowed_keys = _EXPLANATION_SLOTS.get(reason_code, ())
    raw_fact_ids = parsed.get("supporting_fact_ids")
    if not isinstance(raw_fact_ids, list) or not raw_fact_ids:
        return None
    fact_ids: list[int] = []
    evidence = {int(i) for i in evidence_fact_ids}
    for value in raw_fact_ids:
        valid = _valid_int(value)
        if valid is None or valid not in evidence:
            return None  # 证据外的 fact id = 编造，整体拒绝
        fact_ids.append(valid)
    if len(set(fact_ids)) != len(fact_ids):
        return None
    slots = parsed.get("template_slots")
    if not isinstance(slots, dict) or set(slots) - set(allowed_keys):
        return None
    for _slot_key, value in slots.items():
        if not isinstance(value, str) or value not in slot_values_allowed:
            return None
    return {
        "schema_version": EXPLANATION_SCHEMA_VERSION,
        "reason_code": reason_code,
        "supporting_fact_ids": fact_ids,
        "template_slots": dict(slots),
    }


def render_explanation(structured: dict[str, Any], *, counterpart_name: str | None) -> str:
    """确定性模板渲染（R2）：模型只决定选择，文案由模板生成。

    展示名只在服务端按 recipient 当前权限替换（此处 counterpart 来自卡片
    subject/object，由确定性矩阵判定过）；不使用模型自由文本。
    """
    reason_code = structured["reason_code"]
    count = len(structured["supporting_fact_ids"])
    who = counterpart_name or "对方"
    if reason_code == "household_link_available":
        return f"基于你们之间已确认的 {count} 条事实，你和{who}符合创建共同家庭空间的条件。"
    if reason_code == "lineage_request_available":
        return f"基于已确认的 {count} 条事实，你可以向{who}所在的家庭空间申请加入。"
    # 封闭集合兜底（校验器保证不可达）
    return "基于已确认的家庭事实生成该推荐。"


def candidate_digest(kind: str, subject_user_id: int, object_user_id: int) -> str:
    """候选去重摘要：仅基于结构（kind + 双端点），不含 rationale/措辞（F07）。

    相同建议换措辞/换语言不会绕过去重。
    """
    canonical = json.dumps(
        [str(kind), int(subject_user_id), int(object_user_id)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CaseEvaluation:
    """评测用例的结构化结果（R4 报告行）。"""

    case_id: str
    policy_ok: bool
    candidate_valid: bool
    evidence_supported: bool
    ranking_permutation_ok: bool
    fallback_reason: str | None
    outbound_clean: bool
    result_empty: bool
    expected: dict[str, Any] = field(default_factory=dict)
    security_case: bool = True
    candidate_positive: bool = False


@dataclass
class FixtureInput:
    """评测 fixture 的合成输入（不含真实家庭数据）。"""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    provider: dict[str, Any] = field(default_factory=dict)
    card: dict[str, Any] = field(default_factory=dict)
    ranking_card_ids: list[int] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)


def evaluate_case(case: dict[str, Any], *, prompt_max_bytes: int) -> CaseEvaluation:
    """把一条 fixture 用例跑过安全链（投影 → policy → 校验器）。

    仅证明程序合同（fake 输入/输出）；绝不代表真实 provider 质量分数。
    出站检查只针对 provider payload；解释渲染文本是服务端按 recipient 权限
    替换后的呈现层产物，不属于出站面。
    """
    raw = case.get("input", {})
    inp = FixtureInput(
        nodes=list(raw.get("nodes", [])),
        facts=list(raw.get("facts", [])),
        provider=dict(raw.get("provider", {})),
        card=dict(raw.get("card", {})),
        ranking_card_ids=[int(i) for i in raw.get("ranking_card_ids", [])],
        limits=dict(raw.get("limits", {})),
    )
    kind = str(case.get("kind", "candidate"))
    model_output = str(case.get("model_output", ""))
    expected = dict(case.get("expected", {}))
    security_case = bool(expected.get("security_case", True))
    candidate_positive = bool(expected.get("candidate_positive", False))
    case_id = str(case["case_id"])

    def _result(
        *,
        policy_ok: bool = True,
        candidate_valid: bool = False,
        evidence_supported: bool = False,
        ranking_ok: bool = False,
        fallback_reason: str | None = None,
        result_empty: bool = True,
        outbound_clean: bool = True,
    ) -> CaseEvaluation:
        return CaseEvaluation(
            case_id=case_id,
            policy_ok=policy_ok,
            candidate_valid=candidate_valid,
            evidence_supported=evidence_supported,
            ranking_permutation_ok=ranking_ok,
            fallback_reason=fallback_reason,
            outbound_clean=outbound_clean,
            result_empty=result_empty,
            expected=expected,
            security_case=security_case,
            candidate_positive=candidate_positive,
        )

    # 传输层降级（budget/timeout 对抗例）：attempt 从未成功发送 → 产物为空
    transport_error = case.get("transport_error")
    if transport_error:
        return _result(fallback_reason=str(transport_error))

    visible = {int(n["id"]) for n in inp.nodes}
    minor_ids = {int(n["id"]) for n in inp.nodes if n.get("minor")}
    ctx = ProjectionContext(visible, minor_ids=minor_ids)

    # 预算上界（adv-budget）：投影后的 prompt 超过上限 → 不发送（降级）
    max_bytes = int(inp.limits.get("prompt_max_bytes", prompt_max_bytes))
    if kind == "candidate":
        user_content = project_candidate_input(inp.facts, ctx)
    elif kind == "ranking":
        user_content = project_ranking_input(
            [{"card_id": cid, "kind": "household_link"} for cid in inp.ranking_card_ids]
        )
    else:
        user_content = project_explanation_input(inp.card, ctx)
    payload = {"messages": [{"role": "user", "content": user_content}]}
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes:
        return _result(fallback_reason="prompt_too_large")

    provider_kind = inp.provider.get("kind", "cloud")
    decision = outbound_check(
        payload,
        provider_kind=provider_kind,
        local_required=bool(inp.provider.get("local_required", False)),
        cloud_allowed=bool(inp.provider.get("cloud_allowed", True)),
    )
    if decision.action == "block":
        return _result(policy_ok=False, fallback_reason=decision.reason)
    if decision.action == "redact":
        payload = decision.value

    # 出站断言：provider payload 不得携带任何植入的敏感串（R1/AC-1）
    planted = [str(x) for x in (raw.get("plant") or {}).values() if isinstance(x, str) and x]
    outbound_text = json.dumps(payload, ensure_ascii=False)
    outbound_clean = not any(s in outbound_text for s in planted)

    if kind == "candidate":
        items = validate_candidate_output(model_output, ctx)
        if items is None:
            return _result(outbound_clean=outbound_clean)
        return _result(
            candidate_valid=True,
            evidence_supported=True,
            result_empty=False,
            outbound_clean=outbound_clean,
        )
    if kind == "ranking":
        order = validate_ranking_output(model_output, inp.ranking_card_ids)
        if order is None:
            return _result(outbound_clean=outbound_clean)
        return _result(
            evidence_supported=True,
            ranking_ok=True,
            result_empty=False,
            outbound_clean=outbound_clean,
        )
    # explanation
    card = inp.card
    allowed_code = EXPLANATION_REASON_CODES.get(str(card.get("kind", "")))
    evidence_ids = [int(f["id"]) for f in card.get("evidence_facts", [])]
    allowed_slot_values = {
        ctx.codename(int(card[k]))
        for k in ("subject_user_id", "object_user_id")
        if card.get(k) is not None
    }
    allowed_slot_values.discard(None)
    structured = validate_explanation_output(
        model_output,
        reason_code=allowed_code,
        evidence_fact_ids=evidence_ids,
        slot_values_allowed={str(v) for v in allowed_slot_values},
    )
    if structured is None:
        return _result(outbound_clean=outbound_clean)
    return _result(
        evidence_supported=True,
        result_empty=False,
        outbound_clean=outbound_clean,
    )


def case_passed(result: CaseEvaluation) -> bool:
    """单用例通过判定：观测行为必须与 expected 全量一致，且出站干净。"""
    if not result.outbound_clean:
        return False
    expected = result.expected
    checks = {
        "policy_ok": result.policy_ok,
        "candidate_valid": result.candidate_valid,
        "evidence_supported": result.evidence_supported,
        "ranking_permutation_ok": result.ranking_permutation_ok,
        "result_empty": result.result_empty,
    }
    for key, observed in checks.items():
        if key in expected and bool(expected[key]) != observed:
            return False
    if "fallback_reason" in expected and result.fallback_reason != expected["fallback_reason"]:
        return False
    return True


def aggregate(cases: list[CaseEvaluation]) -> dict[str, Any]:
    """聚合：硬安全门槛（100%）与候选召回（正例通过率）分开计算。"""
    security = [c for c in cases if c.security_case]
    positives = [c for c in cases if c.candidate_positive]
    recall = (
        sum(1 for c in positives if c.candidate_valid and not c.result_empty) / len(positives)
        if positives
        else 1.0
    )
    return {
        "hard_gate_passed": all(case_passed(c) for c in security),
        "security_cases_total": len(security),
        "security_cases_failed": sum(1 for c in security if not case_passed(c)),
        "candidate_recall": round(recall, 4),
        "negative_cases_empty_ok": all(
            case_passed(c) for c in cases if c.expected.get("result_empty")
        ),
    }


__all__ = [
    "CANDIDATE_ATOMIC_KINDS",
    "CANDIDATE_PAYLOAD_KEYS",
    "EXPLANATION_REASON_CODES",
    "EXPLANATION_SCHEMA_VERSION",
    "CaseEvaluation",
    "ProjectionContext",
    "aggregate",
    "candidate_digest",
    "case_passed",
    "evaluate_case",
    "outbound_check",
    "project_candidate_input",
    "project_explanation_input",
    "project_ranking_input",
    "render_explanation",
    "validate_candidate_output",
    "validate_explanation_output",
    "validate_ranking_output",
]
