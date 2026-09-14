"""管家称谓自主优化（任务 09-13-steward-terminology-autonomy）。

职责（B design.md）：
- **生产发现**（core 确定性短事务内，零模型）：对当前 PFV 已确认路径计算
  baseline；确定性改善（本人明确用词、长链泛化已生效的可选保留）落为
  ``StewardTermProjection`` 与可选 ``term_preference`` 建议（notify=False，
  不逐条打扰）。
- **模型产物语义校验**（写回前服务端重验）：不信任模型自报概念码——从真实
  路径取真值，用四级词条/词素/长幼规则核验 term 含义；无法归一为可信语义的
  表达放弃，不转待批准。
- **稳定抑制**：恢复原叫法按 ``suppression_key``（account/space/target + 已验证
  语义 + 规范词）跨建议/证据/prompt 版本防重现。
- **有效词选择器**（只读）：个人词条 → 生效空间词条 无条件优先；否则消费
  有效自动投影；无自动项返回 baseline。

边界：不写 SourceFact/TermEntry/TermUsage；自动产物不伪造人类用词证据；
普通 GET 零模型调用；terminology 关闭时全部回退确定性显示。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models.account import Account
from app.models.personal_family_view import PersonalFamilyView, PersonalFamilyViewEdge
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember
from app.models.steward import StewardJob, StewardTermProjection, StewardTermSuppression
from app.models.steward_suggestion import StewardSuggestion
from app.models.term_registry import BUILTIN_TERM_SEEDS, TermEntry, TermUsage
from app.services import terms
from app.services.derived_facts import steps_from_json
from app.services.relationship_graph import load_birth_years, load_graph
from app.services.relationship_resolver import (
    concept_code_for_path,
    describe_path,
    resolve_relationship,
)
from app.utils.timeutil import utcnow

RULE_VERSION = "terminology-v2"
PROMPT_VERSION = "terminology-prompt-v2"

REASON_SYNONYM = "synonym"
REASON_SHORTER_CHAIN = "shorter_chain"
REASON_PREFERRED_USAGE = "preferred_usage"

# 参与自动优化的 baseline 来源（personal/space 用户自定义永不覆盖）
_OVERRIDABLE_BASELINE_SOURCES = ("locale", "system", "derived", "structural")

TERM_MAX_LENGTH = 64


def _canonical_hash(value: Any) -> str:
    import json

    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def suppression_key_for(
    *, viewer_account_id: int, space_id: int, target_user_id: int, concept_code: str, term: str
) -> str:
    """稳定抑制键：不含路径事实 ID/revision、词条 revision、prompt 或反馈版本。

    同一关系换等价证据路径/词条升级不能绕过恢复过的拒绝。
    """
    normalized = "".join(term.split())
    payload = [
        int(viewer_account_id),
        int(space_id),
        int(target_user_id),
        _suppression_concept(concept_code),
        normalized,
    ]
    return _canonical_hash(payload)


def _suppression_concept(code: str) -> str:
    """A biological shared-parent path and a direct sibling have the same displayed role."""
    tokens = code.split("-")
    if len(tokens) >= 2 and tokens[0] in ("U", "Um", "Uf") and tokens[1] in ("D", "Dm", "Df"):
        tokens = ["B" + tokens[1][1:], *tokens[2:]]
    return "-".join(tokens)


def has_suppression(
    db: Session, *, viewer_account_id: int, space_id: int, target_user_id: int, key: str
) -> bool:
    from app.models.steward import StewardTermSuppression

    return (
        db.scalar(
            select(StewardTermSuppression.id).where(
                StewardTermSuppression.viewer_account_id == viewer_account_id,
                StewardTermSuppression.space_id == space_id,
                StewardTermSuppression.target_user_id == target_user_id,
                StewardTermSuppression.suppression_key == key,
            )
        )
        is not None
    )


# ---- 语义校验（allowlist：只认服务端可解析的含义）----


def _allowed_terms_for_code(
    db: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
    variant_context: terms.VariantContext | None = None,
) -> set[str]:
    """该概念码上服务端可核验的全部自然词（allowlist）。

    - 四级词条命中词（personal=本人才生效；他人 personal 不进入）；
    - 长幼消歧词：仅当出生数据可比、能服务端判定长幼时纳入具体词；
    - 不包含任何性别/亚型证据中没有的限定词。
    """
    allowed: set[str] = set()
    resolved = terms.resolve_term(
        db, account_id=account_id, space_id=space_id, concept_code=concept_code
    )
    if resolved.term:
        allowed.add(resolved.term)
    if resolved.source_level in (terms.TERM_LEVEL_PERSONAL, terms.TERM_LEVEL_SPACE):
        return allowed
    # The same bundled vocabulary used by migration/seed is also the semantic
    # allowlist. Existing databases need not be reseeded to validate a new alias.
    locale = terms.space_locale(db, space_id)
    allowed.update(
        text
        for level, entry_locale, code, text in BUILTIN_TERM_SEEDS
        if code == concept_code and (level == "system" or entry_locale == locale)
    )
    rows = db.scalars(
        select(TermEntry).where(
            TermEntry.concept_code == concept_code,
            TermEntry.status == "active",
            TermEntry.level.in_(("system", "locale", "space")),
        )
    ).all()
    for row in rows:
        if row.level == terms.TERM_LEVEL_SPACE and row.space_id != space_id:
            continue
        if row.level == terms.TERM_LEVEL_LOCALE and row.locale != terms.space_locale(db, space_id):
            continue
        allowed.add(row.term)
    if variant_context is not None:
        variant = terms._sibling_variant_term(concept_code, variant_context)
        if variant:
            allowed.add(variant)
    return allowed


def term_semantics_valid(
    db: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
    term: str,
    variant_context: terms.VariantContext | None = None,
) -> bool:
    """核验 term 是否可由给定路径语义 + 现有词条/词素组合出来。

    - 精确词：命中 allowlist（含长幼可判时的具体词）；
    - 组合词：「的」连接，逐段覆盖最长已命名前缀或残链小词；
    - 其他一切（新造词、跨亚型、无依据长幼、歧义）→ False。
    """
    cleaned = term.strip()
    if not cleaned or len(cleaned) > TERM_MAX_LENGTH:
        return False
    tokens = concept_code.split("-")
    if "SELF" in tokens:  # pragma: no cover - SELF 不产生称谓建议
        return False
    allowed_full = _candidate_terms(
        db,
        account_id=account_id,
        space_id=space_id,
        concept_code=concept_code,
        variant_context=variant_context,
    )
    return cleaned in allowed_full


# ---- 依据摘要 ----


def projection_semantic_hash(
    *,
    viewer_account_id: int,
    space_id: int,
    target_user_id: int,
    concept_code: str | None,
    path_fact_revisions: list[list[int]],
    term_registry_hash: str,
    root_user_id: int | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    return _canonical_hash(
        [
            RULE_VERSION,
            int(viewer_account_id),
            int(space_id),
            int(target_user_id),
            concept_code,
            path_fact_revisions,
            term_registry_hash,
            root_user_id,
            context,
        ]
    )


def request_hash_for(semantic_hash: str) -> str:
    return _canonical_hash([PROMPT_VERSION, semantic_hash])


# ---- 生产发现（core 短事务内；零模型）----


def _pfv_rows(db: Session, *, space_id: int) -> list[tuple[int, int, int]]:
    """(viewer_account_id, root_user_id, view_id) 三元组（active 成员视图）。"""
    rows = db.execute(
        select(
            PersonalFamilyView.viewer_account_id,
            PersonalFamilyView.root_user_id,
            PersonalFamilyView.id,
        )
        .join(Account, Account.id == PersonalFamilyView.viewer_account_id)
        .join(
            SpaceMember,
            (SpaceMember.user_id == Account.user_id)
            & (SpaceMember.space_id == PersonalFamilyView.space_id),
        )
        .where(
            PersonalFamilyView.space_id == space_id,
            PersonalFamilyView.root_user_id == Account.user_id,
            PersonalFamilyView.status == "current",
            SpaceMember.status == "active",
        )
        .order_by(PersonalFamilyView.viewer_account_id)
    ).all()
    return [(int(a), int(r), int(v)) for a, r, v in rows]


def _explicit_usage_term(
    db: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
    variant_context: terms.VariantContext | None = None,
) -> tuple[str, int] | None:
    """本人明确用词：该账号在此空间选过、且属于同一概念码的四级词条词。

    只读 TermUsage/TermEntry（不调用 BehaviorProjection rebuild，不推断偏好）。
    """
    entries = db.scalars(
        select(TermEntry)
        .join(TermUsage, TermUsage.term_entry_id == TermEntry.id)
        .where(
            TermUsage.account_id == account_id,
            TermUsage.space_id == space_id,
            TermEntry.concept_code == concept_code,
        )
        .order_by(TermUsage.created_at.desc(), TermUsage.id.desc())
    ).all()
    for entry in entries:
        if term_semantics_valid(
            db,
            account_id=account_id,
            space_id=space_id,
            concept_code=concept_code,
            term=entry.term,
            variant_context=variant_context,
        ):
            return entry.term, int(entry.id)
    return None


def _age_order_for_path(code: str, context: terms.VariantContext) -> str:
    base = terms.sibling_base_hop(code.split("-"))
    if base is None or base[0] >= len(context.path):
        return "unknown"
    step = context.path[base[0]]
    reference = context.viewer_user_id if base[0] else int(step["from"])
    return terms._age_order(context.births, reference, int(step["to"])) or "unknown"


def _candidate_terms(
    db: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
    variant_context: terms.VariantContext | None = None,
) -> set[str]:
    allowed = _allowed_terms_for_code(
        db,
        account_id=account_id,
        space_id=space_id,
        concept_code=concept_code,
        variant_context=variant_context,
    )
    tokens = concept_code.split("-")
    for cut in range(1, len(tokens)):
        words = [terms.residual_word_for(token) for token in tokens[cut:]]
        if any(word is None for word in words):
            continue
        for prefix in _allowed_terms_for_code(
            db,
            account_id=account_id,
            space_id=space_id,
            concept_code="-".join(tokens[:cut]),
            variant_context=(
                terms.VariantContext(
                    viewer_user_id=variant_context.viewer_user_id,
                    path=variant_context.path[:cut],
                    births=variant_context.births,
                )
                if variant_context is not None
                else None
            ),
        ):
            combined = "的".join([prefix, *(word for word in words if word is not None)])
            if len(combined) <= TERM_MAX_LENGTH:
                allowed.add(combined)
    return allowed


def current_target_context(
    db: Session,
    *,
    viewer_account_id: int,
    root_user_id: int,
    space_id: int,
    target_user_id: int,
    path: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Read a current authorized path, without trusting a saved PFV or model echo.

    The fingerprint contains only this target's inputs. Output revisions and unrelated
    facts never enter it; raw birth values never enter stored or outbound contexts.
    """
    account = db.get(Account, viewer_account_id)
    if account is None or account.user_id != root_user_id or root_user_id == target_user_id:
        return None
    if (
        db.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == root_user_id,
                SpaceMember.status == "active",
            )
        )
        is None
    ):
        return None
    if path is None:
        result = resolve_relationship(
            db,
            viewer_user_id=root_user_id,
            target_user_id=target_user_id,
            space_id=space_id,
        )
        if not result.found:
            return None
        path = [step.to_json() for step in result.main_path]
    if not path:
        return None
    graph = load_graph(db, viewer_user_id=root_user_id, space_id=space_id)
    node_id = root_user_id
    fact_revisions: list[list[int]] = []
    persons = {root_user_id}
    for step in path:
        if not isinstance(step, dict) or step.get("from") != node_id:
            return None
        fact_id = step.get("fact_id")
        # Automatic optimization is restricted to confirmed paths in this release.
        if type(fact_id) is not int or fact_id <= 0:
            return None
        match = next(
            (
                edge
                for edge in graph.adjacency.get(node_id, [])
                if (
                    edge.to_id == step.get("to")
                    and edge.fact_id == fact_id
                    and edge.edge_type == step.get("edge_type")
                    and edge.subtype == step.get("subtype")
                    and edge.direction == step.get("direction")
                )
            ),
            None,
        )
        if match is None or match.to_id in persons:
            return None
        fact = db.get(SourceFact, fact_id)
        if fact is None:
            return None
        fact_revisions.append([fact_id, fact.revision])
        node_id = match.to_id
        persons.add(node_id)
    if node_id != target_user_id:
        return None
    steps = steps_from_json(path)
    concept = concept_code_for_path(steps, graph.node_genders)
    if concept is None or concept == "SELF":
        return None
    variants = terms.VariantContext(
        viewer_user_id=root_user_id,
        path=path,
        births=load_birth_years(
            db, viewer_user_id=root_user_id, space_id=space_id, user_ids=persons
        ),
    )
    baseline = terms.resolve_term_or_structural(
        db,
        account_id=viewer_account_id,
        space_id=space_id,
        concept_code=concept,
        structural_description=describe_path(steps, graph.node_genders),
        variant_context=variants,
    )
    tokens = concept.split("-")
    term_hash = terms.term_registry_hash(
        db,
        space_id=space_id,
        account_id=viewer_account_id,
        concept_codes={"-".join(tokens[:cut]) for cut in range(1, len(tokens) + 1)},
    )
    usage_rows = db.execute(
        select(TermUsage.id, TermEntry.id, TermEntry.revision, TermEntry.term)
        .join(TermEntry, TermEntry.id == TermUsage.term_entry_id)
        .where(
            TermUsage.account_id == viewer_account_id,
            TermUsage.space_id == space_id,
            TermEntry.concept_code == concept,
        )
        .order_by(TermUsage.id)
    ).all()
    suppressions = list(
        db.scalars(
            select(StewardTermSuppression.suppression_key)
            .where(
                StewardTermSuppression.viewer_account_id == viewer_account_id,
                StewardTermSuppression.space_id == space_id,
                StewardTermSuppression.target_user_id == target_user_id,
            )
            .order_by(StewardTermSuppression.suppression_key)
        )
    )
    age_order = _age_order_for_path(concept, variants)
    allowed = _candidate_terms(
        db,
        account_id=viewer_account_id,
        space_id=space_id,
        concept_code=concept,
        variant_context=variants,
    )
    allowed = {
        term
        for term in allowed
        if not has_suppression(
            db,
            viewer_account_id=viewer_account_id,
            space_id=space_id,
            target_user_id=target_user_id,
            key=suppression_key_for(
                viewer_account_id=viewer_account_id,
                space_id=space_id,
                target_user_id=target_user_id,
                concept_code=concept,
                term=term,
            ),
        )
    }
    usage = _explicit_usage_term(
        db,
        account_id=viewer_account_id,
        space_id=space_id,
        concept_code=concept,
        variant_context=variants,
    )
    semantic = projection_semantic_hash(
        viewer_account_id=viewer_account_id,
        root_user_id=root_user_id,
        space_id=space_id,
        target_user_id=target_user_id,
        concept_code=concept,
        path_fact_revisions=fact_revisions,
        term_registry_hash=term_hash,
        context={
            "policy_version": config.POLICY_VERSION,
            "path": path,
            "age_order": age_order,
            "baseline": baseline,
            "usage": [list(row) for row in usage_rows],
            "suppressions": suppressions,
            "allowed_terms": sorted(allowed),
        },
    )
    return {
        "target_user_id": target_user_id,
        "concept_code": concept,
        "path": path,
        "baseline_term": baseline["term"],
        "baseline_source": baseline["source_level"],
        "age_order": age_order,
        "allowed_terms": sorted(allowed),
        "preferred_term": usage[0] if usage is not None and usage[0] in allowed else None,
        "semantic_hash": semantic,
        "request_hash": request_hash_for(semantic),
    }


def upsert_projection(
    db: Session,
    *,
    space_id: int,
    viewer_account_id: int,
    root_user_id: int,
    target_user_id: int,
    concept_code: str | None,
    semantic_hash: str,
    baseline_term: str | None,
    baseline_source: str | None,
    term: str | None,
    origin: str | None,
    source_model_call_id: int | None = None,
    now: Any = None,
) -> tuple[Any, bool]:
    """单行投影写规则（B design.md §2 表）。

    返回 (projection, changed)。core 重算且语义未变只刷 baseline 元数据，
    绝不清除有效 term/origin/last_checked（模型成果保留）。
    """
    from app.models.steward import StewardTermProjection

    now = now or utcnow()
    row = db.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.space_id == space_id,
            StewardTermProjection.viewer_account_id == viewer_account_id,
            StewardTermProjection.root_user_id == root_user_id,
            StewardTermProjection.target_user_id == target_user_id,
        )
    )
    created = False
    if row is None:
        row = StewardTermProjection(
            space_id=space_id,
            viewer_account_id=viewer_account_id,
            root_user_id=root_user_id,
            target_user_id=target_user_id,
            concept_code=concept_code,
            semantic_hash=semantic_hash,
            rule_version=RULE_VERSION,
            revision=1,
            status="unchanged",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        created = True
    before = (
        row.semantic_hash,
        row.baseline_term,
        row.baseline_source,
        row.term,
        row.origin,
        row.status,
        row.rule_version,
    )
    if row.semantic_hash != semantic_hash:
        # 相关依据改变：旧 term 先失效；稳定拒绝记录另行继续有效
        row.semantic_hash = semantic_hash
        if row.status == "active" and row.term is not None:
            row.status = "stale"
    row.rule_version = RULE_VERSION
    row.concept_code = concept_code
    row.baseline_term = baseline_term
    row.baseline_source = baseline_source
    row.request_hash = request_hash_for(semantic_hash)
    if term is not None:
        # 相同词幂等：CAS 更新不清反馈/last_checked
        if row.term != term or row.origin != origin or row.status != "active":
            row.term = term
            row.origin = origin
            row.source_model_call_id = source_model_call_id
            row.status = "active"
    after = (
        row.semantic_hash,
        row.baseline_term,
        row.baseline_source,
        row.term,
        row.origin,
        row.status,
        row.rule_version,
    )
    changed = before != after
    if changed and not created:
        row.revision += 1
    row.updated_at = now
    db.flush()
    return row, created or changed


def upsert_term_preference_suggestion(
    db: Session,
    *,
    space_id: int,
    viewer_account_id: int,
    subject_user_id: int,
    object_user_id: int,
    concept_code: str,
    term: str,
    projection_id: int,
    projection_revision: int,
    semantic_hash: str,
    reason_code: str,
    policy_version: str,
    origin: str,
    now: Any = None,
) -> tuple[Any, bool]:
    """可选偏好建议（notify=False）：不逐条创建待办通知。

    value 封闭字段：concept_code/term/target/projection_id/semantic_identity/
    suppression_key/reason_code。去重含本人+空间+目标+语义+规范词。
    """
    from app.services import steward_suggestions

    now = now or utcnow()
    key = suppression_key_for(
        viewer_account_id=viewer_account_id,
        space_id=space_id,
        target_user_id=object_user_id,
        concept_code=concept_code,
        term=term,
    )
    if has_suppression(
        db,
        viewer_account_id=viewer_account_id,
        space_id=space_id,
        target_user_id=object_user_id,
        key=key,
    ):
        return None, False
    value_json = {
        "concept_code": concept_code,
        "term": term,
        "target_user_id": int(object_user_id),
        "projection_id": int(projection_id),
        "projection_revision": int(projection_revision),
        "semantic_identity": semantic_hash,
        "suppression_key": key,
        "reason_code": reason_code,
    }
    # 同语义同词的历史终态（含 resolved）也去重：恢复过/保留过的建议不重生
    existing_any = db.scalar(
        select(StewardSuggestion)
        .where(
            StewardSuggestion.space_id == space_id,
            StewardSuggestion.kind == "term_preference",
            StewardSuggestion.viewer_account_id == viewer_account_id,
            StewardSuggestion.subject_user_id == subject_user_id,
            StewardSuggestion.object_user_id == object_user_id,
            StewardSuggestion.value_json["term"].as_string() == term,
            StewardSuggestion.value_json["concept_code"].as_string() == concept_code,
        )
        .order_by(StewardSuggestion.id.desc())
        .limit(1)
    )
    if existing_any is not None:
        if existing_any.status == "proposed" and existing_any.value_json != value_json:
            existing_any.value_json = value_json
            existing_any.revision += 1
            existing_any.updated_at = now
        return existing_any, False
    suggestion, created = steward_suggestions.upsert_suggestion(
        db,
        space_id=space_id,
        origin=origin,
        kind="term_preference",
        subject_user_id=subject_user_id,
        object_user_id=object_user_id,
        value_json=value_json,
        evidence_json={"facts": []},
        policy_version=policy_version,
        recipient_account_ids=[viewer_account_id],
        viewer_account_id=viewer_account_id,
        notify=False,
        now=now,
    )
    # Optional controls remain available as long as their projection is current.
    # Evidence changes and explicit feedback, rather than a timer, retire them.
    suggestion.expires_at = None
    return suggestion, created


def request_projection_refresh(db: Session, *, space_id: int, viewer_account_ids: set[int]) -> None:
    """Invalidate and enqueue in the writer's transaction; never open another writer."""
    from app.services.domain_events import emit

    for account_id in sorted(viewer_account_ids):
        emit(
            db,
            event_type="term.steward_updated",
            aggregate_type="account",
            aggregate_id=account_id,
            space_id=space_id,
            payload={"account_id": account_id},
        )


def projection_state_hash(db: Session, *, account_id: int, space_id: int) -> str:
    """PFV freshness includes automatic output and its effective feature switch."""
    from app.services.steward_assist import assist_enabled

    rows = db.scalars(
        select(StewardTermProjection)
        .where(
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.space_id == space_id,
            StewardTermProjection.term.is_not(None),
        )
        .order_by(StewardTermProjection.id)
    ).all()
    return _canonical_hash(
        [
            RULE_VERSION,
            assist_enabled(db, space_id, "terminology")
            if any(r.origin == "model" for r in rows)
            else None,
            [[r.id, r.revision, r.semantic_hash, r.status, r.rule_version] for r in rows],
        ]
    )


def run_deterministic_scan(db: Session, *, job: StewardJob, now: Any = None) -> dict[str, int]:
    """Produce only from current authorized baselines, never from our own display output."""
    now = now or utcnow()
    stats = {"projections": 0, "suggestions": 0}
    changed_viewers: set[int] = set()
    for viewer_account_id, root_user_id, view_id in _pfv_rows(db, space_id=job.space_id):
        edges = db.scalars(
            select(PersonalFamilyViewEdge).where(
                PersonalFamilyViewEdge.view_id == view_id,
                PersonalFamilyViewEdge.inclusion_reason_code == "confirmed_path",
            )
        ).all()
        for edge in edges:
            target = current_target_context(
                db,
                viewer_account_id=viewer_account_id,
                root_user_id=root_user_id,
                space_id=job.space_id,
                target_user_id=int(edge.to_user_id),
                path=edge.path_json,
            )
            if target is None:
                continue
            baseline_source = target["baseline_source"]
            if baseline_source not in _OVERRIDABLE_BASELINE_SOURCES:
                continue
            baseline = target["baseline_term"]
            preferred = target["preferred_term"]
            override = preferred if preferred and preferred != baseline else None
            reason = REASON_PREFERRED_USAGE if override else REASON_SHORTER_CHAIN
            suggestion_term = override or (baseline if baseline_source == "derived" else None)
            previous = db.scalar(
                select(StewardTermProjection).where(
                    StewardTermProjection.space_id == job.space_id,
                    StewardTermProjection.viewer_account_id == viewer_account_id,
                    StewardTermProjection.root_user_id == root_user_id,
                    StewardTermProjection.target_user_id == edge.to_user_id,
                )
            )
            had_override = previous is not None and previous.term is not None
            projection, changed = upsert_projection(
                db,
                space_id=job.space_id,
                viewer_account_id=viewer_account_id,
                root_user_id=root_user_id,
                target_user_id=int(edge.to_user_id),
                concept_code=target["concept_code"],
                semantic_hash=target["semantic_hash"],
                baseline_term=baseline,
                baseline_source=baseline_source,
                term=override,
                origin="deterministic" if override else None,
                now=now,
            )
            stats["projections"] += 1
            if changed and (had_override or override is not None):
                changed_viewers.add(viewer_account_id)
            if suggestion_term is None:
                continue
            _suggestion, created = upsert_term_preference_suggestion(
                db,
                space_id=job.space_id,
                viewer_account_id=viewer_account_id,
                subject_user_id=root_user_id,
                object_user_id=int(edge.to_user_id),
                concept_code=target["concept_code"],
                term=suggestion_term,
                projection_id=projection.id,
                projection_revision=projection.revision,
                semantic_hash=target["semantic_hash"],
                reason_code=reason,
                policy_version=job.policy_version,
                origin="deterministic",
                now=now,
            )
            stats["suggestions"] += int(created)
    request_projection_refresh(db, space_id=job.space_id, viewer_account_ids=changed_viewers)
    return stats


# ---- Model discovery: bounded output, current authorization, per-target history ----


def collect_model_groups(
    db: Session,
    *,
    space_id: int,
    max_groups: int,
    max_targets: int,
) -> list[dict[str, Any]]:
    if max_groups <= 0 or max_targets <= 0:
        return []
    from app.services.steward_assist import terminology_target_retryable

    candidates: list[tuple[Any, int, dict[str, Any]]] = []
    for viewer_account_id, root_user_id, view_id in _pfv_rows(db, space_id=space_id):
        targets: list[tuple[Any, int, dict[str, Any]]] = []
        edges = db.scalars(
            select(PersonalFamilyViewEdge).where(
                PersonalFamilyViewEdge.view_id == view_id,
                PersonalFamilyViewEdge.inclusion_reason_code == "confirmed_path",
            )
        ).all()
        for edge in edges:
            target = current_target_context(
                db,
                viewer_account_id=viewer_account_id,
                root_user_id=root_user_id,
                space_id=space_id,
                target_user_id=int(edge.to_user_id),
                path=edge.path_json,
            )
            if target is None or target["baseline_source"] not in _OVERRIDABLE_BASELINE_SOURCES:
                continue
            baseline = target["baseline_term"] or ""
            if target["preferred_term"] and target["preferred_term"] != baseline:
                continue  # An explicit prior choice is authoritative over model rewording.
            alternatives = [
                t for t in target["allowed_terms"] if t != baseline and len(t) <= len(baseline)
            ]
            if not alternatives:
                continue
            row = db.scalar(
                select(StewardTermProjection).where(
                    StewardTermProjection.space_id == space_id,
                    StewardTermProjection.viewer_account_id == viewer_account_id,
                    StewardTermProjection.root_user_id == root_user_id,
                    StewardTermProjection.target_user_id == edge.to_user_id,
                )
            )
            if row is not None and row.last_checked_hash == target["request_hash"]:
                continue
            if not terminology_target_retryable(
                db,
                space_id=space_id,
                viewer_account_id=viewer_account_id,
                root_user_id=root_user_id,
                target_user_id=int(edge.to_user_id),
                semantic_hash=target["semantic_hash"],
                request_hash=target["request_hash"],
            ):
                continue
            target["projection_id"] = row.id if row is not None else None
            attempted_at = row.last_attempt_at.isoformat() if row and row.last_attempt_at else ""
            targets.append((attempted_at, int(edge.to_user_id), target))
        if targets:
            targets.sort(key=lambda entry: (entry[0], entry[1]))
            candidates.append(
                (
                    targets[0][0],
                    viewer_account_id,
                    {
                        "viewer_account_id": viewer_account_id,
                        "root_user_id": root_user_id,
                        "space_id": space_id,
                        "targets": [entry[2] for entry in targets[:max_targets]],
                    },
                )
            )
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in candidates[:max_groups]]


def targets_digest(targets: list[dict[str, Any]]) -> str:
    return _canonical_hash([int(t["target_user_id"]) for t in targets])[:16]


# ---- 模型输入投影 ----


def project_terminology_input(db: Session, group: dict[str, Any]) -> str:
    """Bounded, de-identified, untrusted terminology data with an exact request identity."""
    targets = [
        {
            "target_ref": f"t{index + 1:03d}",
            "concept_code": target["concept_code"],
            "baseline_term": target["baseline_term"],
            "age_order": target.get("age_order", "unknown"),
            "allowed_terms": target.get("allowed_terms", []),
            "semantic_hash": target["semantic_hash"],
        }
        for index, target in enumerate(group["targets"])
    ]
    context_hash = _canonical_hash(
        [
            PROMPT_VERSION,
            group["viewer_account_id"],
            group["root_user_id"],
            group.get("space_id"),
            targets,
        ]
    )
    return json.dumps(
        {"version": 1, "context_hash": context_hash, "targets": targets}, ensure_ascii=False
    )


def validate_model_output(
    db: Session,
    *,
    text: str,
    group: dict[str, Any],
    space_id: int,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "context_hash", "items"}
        or type(payload.get("version")) is not int
        or payload["version"] != 1
        or payload.get("context_hash")
        != json.loads(project_terminology_input(db, group))["context_hash"]
    ):
        return None
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > len(group["targets"]):
        return None
    by_ref = {f"t{index + 1:03d}": target for index, target in enumerate(group["targets"])}
    seen_refs: set[str] = set()
    accepted: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "target_ref",
            "concept_code",
            "term",
            "reason_code",
        }:
            return None
        ref = item.get("target_ref")
        if not isinstance(ref, str) or ref not in by_ref or ref in seen_refs:
            return None
        seen_refs.add(ref)
        target = by_ref[ref]
        current = current_target_context(
            db,
            viewer_account_id=int(group["viewer_account_id"]),
            root_user_id=int(group["root_user_id"]),
            space_id=space_id,
            target_user_id=int(target["target_user_id"]),
            path=target["path"],
        )
        if current is None or current["semantic_hash"] != target["semantic_hash"]:
            return None
        term_text = item.get("term")
        reason = item.get("reason_code")
        if (
            not isinstance(term_text, str)
            or reason not in (REASON_SYNONYM, REASON_SHORTER_CHAIN, REASON_PREFERRED_USAGE)
            or item.get("concept_code") != current["concept_code"]
        ):
            continue
        term_text = term_text.strip()
        baseline = current["baseline_term"] or ""
        if (
            not term_text
            or len(term_text) > TERM_MAX_LENGTH
            or len(term_text) > len(baseline)
            or term_text == baseline
            or term_text not in current["allowed_terms"]
            or (reason == REASON_PREFERRED_USAGE and term_text != current["preferred_term"])
            or (reason == REASON_SHORTER_CHAIN and len(term_text) >= len(baseline))
        ):
            continue
        accepted.append(
            {
                "target_user_id": int(target["target_user_id"]),
                "concept_code": current["concept_code"],
                "term": term_text,
                "reason_code": reason,
                "semantic_hash": current["semantic_hash"],
            }
        )
    return {"items": accepted}


# ---- 有效词选择器（只读；PFV/resolve/呈现服务统一消费）----


def effective_override(
    db: Session,
    *,
    account_id: int,
    root_user_id: int,
    space_id: int,
    target_user_id: int,
    concept_code: str | None,
    baseline_term: str | None,
    baseline_source: str | None,
    path: list[dict[str, Any]] | None = None,
) -> str | None:
    """结合当前授权依据选择有效自动词；无有效自动项返回 None（用 baseline）。

    personal/space 词条无条件优先：调用方在 baseline_source 属于这两层时
    不应调用本选择器（或得到 None 之外的覆盖也绝不会发生——投影只针对
    可覆盖 baseline 建立）。
    """
    if concept_code is None or concept_code == "SELF":
        return None
    if baseline_source is not None and baseline_source not in _OVERRIDABLE_BASELINE_SOURCES:
        return None
    from app.models.steward import StewardTermProjection

    row = db.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.space_id == space_id,
            StewardTermProjection.viewer_account_id == account_id,
            StewardTermProjection.root_user_id == root_user_id,
            StewardTermProjection.target_user_id == target_user_id,
            StewardTermProjection.status == "active",
        )
    )
    if (
        row is None
        or row.term is None
        or row.concept_code != concept_code
        or row.rule_version != RULE_VERSION
    ):
        return None
    if row.origin == "model":
        from app.services.steward_assist import assist_enabled

        if not assist_enabled(db, space_id, "terminology"):
            return None
    if row.baseline_term is not None and row.baseline_term != baseline_term:
        # baseline 漂移：旧自动词失效，回退当前 baseline
        return None
    current = current_target_context(
        db,
        viewer_account_id=account_id,
        root_user_id=root_user_id,
        space_id=space_id,
        target_user_id=target_user_id,
        path=path,
    )
    if (
        current is None
        or current["semantic_hash"] != row.semantic_hash
        or current["concept_code"] != concept_code
        or current["baseline_source"] not in _OVERRIDABLE_BASELINE_SOURCES
        or row.term not in current["allowed_terms"]
    ):
        return None
    return row.term


__all__ = [
    "RULE_VERSION",
    "collect_model_groups",
    "effective_override",
    "has_suppression",
    "project_terminology_input",
    "projection_semantic_hash",
    "request_hash_for",
    "run_deterministic_scan",
    "suppression_key_for",
    "targets_digest",
    "term_semantics_valid",
    "upsert_projection",
    "upsert_term_preference_suggestion",
    "validate_model_output",
]
