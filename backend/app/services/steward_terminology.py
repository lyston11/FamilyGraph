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
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.personal_family_view import PersonalFamilyView, PersonalFamilyViewEdge
from app.models.steward import StewardJob
from app.models.steward_suggestion import StewardSuggestion
from app.models.term_registry import TermEntry, TermUsage
from app.models.user import User
from app.services import terms
from app.utils.timeutil import utcnow

RULE_VERSION = "terminology-v1"

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
        str(concept_code),
        normalized,
    ]
    return _canonical_hash(payload)


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
    allowed_full = _allowed_terms_for_code(
        db,
        account_id=account_id,
        space_id=space_id,
        concept_code=concept_code,
        variant_context=variant_context,
    )
    if cleaned in allowed_full:
        return True
    segments = cleaned.split("的")
    for cut in range(1, len(tokens)):
        prefix = "-".join(tokens[:cut])
        residual_tokens = tokens[cut:]
        if len(segments) != 1 + len(residual_tokens):
            continue
        prefix_resolved = terms.resolve_term(
            db, account_id=account_id, space_id=space_id, concept_code=prefix
        )
        if prefix_resolved.term is None or prefix_resolved.term not in (
            _allowed_terms_for_code(
                db,
                account_id=account_id,
                space_id=space_id,
                concept_code=prefix,
            )
        ):
            continue
        residual_words: list[str] = []
        ok = True
        for token in residual_tokens:
            word = terms.residual_word_for(token)
            if word is None:
                ok = False
                break
            residual_words.append(word)
        if ok and "的".join([prefix_resolved.term, *residual_words]) == cleaned:
            return True
    return False


# ---- 依据摘要 ----


def projection_semantic_hash(
    *,
    viewer_account_id: int,
    space_id: int,
    target_user_id: int,
    concept_code: str | None,
    path_fact_revisions: list[list[int]],
    term_registry_hash: str,
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
        ]
    )


def request_hash_for(semantic_hash: str) -> str:
    return _canonical_hash([RULE_VERSION, semantic_hash])


# ---- 生产发现（core 短事务内；零模型）----


def _pfv_rows(db: Session, *, space_id: int) -> list[tuple[int, int, int]]:
    """(viewer_account_id, root_user_id, view_id) 三元组（active 成员视图）。"""
    rows = db.execute(
        select(
            PersonalFamilyView.viewer_account_id,
            PersonalFamilyView.root_user_id,
            PersonalFamilyView.id,
        ).where(PersonalFamilyView.space_id == space_id)
    ).all()
    return [(int(a), int(r), int(v)) for a, r, v in rows]


def _edge_fact_revisions(path: list[dict[str, Any]]) -> list[list[int]]:
    return [
        [int(step["fact_id"]), 0]
        for step in path
        if isinstance(step.get("fact_id"), int) and step["fact_id"] > 0
    ]


def _explicit_usage_term(
    db: Session, *, account_id: int, space_id: int, concept_code: str
) -> tuple[str, int] | None:
    """本人明确用词：该账号在此空间选过、且属于同一概念码的四级词条词。

    只读 TermUsage/TermEntry（不调用 BehaviorProjection rebuild，不推断偏好）。
    """
    usage_rows = db.scalars(
        select(TermUsage.term_entry_id).where(
            TermUsage.account_id == account_id, TermUsage.space_id == space_id
        )
    ).all()
    if not usage_rows:
        return None
    entry_ids = list(usage_rows)
    for entry_id in entry_ids:
        entry = db.get(TermEntry, int(entry_id))
        # 本人明确选择即证据：space 候选可能因晋升规则处于 superseded，
        # 但不改变“该账号在该空间选过这个词”的事实。
        if entry is None:
            continue
        if entry.concept_code != concept_code:
            continue
        return entry.term, int(entry.id)
    return None


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
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        created = True
    if row.semantic_hash != semantic_hash:
        # 相关依据改变：旧 term 先失效；稳定拒绝记录另行继续有效
        row.semantic_hash = semantic_hash
        if row.status == "active" and row.term is not None:
            row.status = "stale"
        row.revision += 1
    row.concept_code = concept_code
    row.baseline_term = baseline_term
    row.baseline_source = baseline_source
    if row.request_hash is None:
        row.request_hash = request_hash_for(semantic_hash)
    if term is not None:
        # 相同词幂等：CAS 更新不清反馈/last_checked
        if row.term != term or row.origin != origin:
            row.term = term
            row.origin = origin
            row.source_model_call_id = source_model_call_id
            row.status = "active"
            row.revision += 1
    row.updated_at = now
    db.flush()
    return row, created or row.revision > 1


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
        select(StewardSuggestion.id)
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
        return None, False
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
    return suggestion, created


def run_deterministic_scan(db: Session, *, job: StewardJob, now: Any = None) -> dict[str, int]:
    """core 完成PFV 后的有界称谓扫描（确定性来源；零模型调用）。

    - baseline 已是长链泛化（derived）：自动生效，产出可选保留建议；
    - 本人明确 TermUsage 提供合法优选词且未显式成词条：deterministic override；
    - 无改善不造建议；显式个人/空间词条目标跳过。
    """
    now = now or utcnow()
    stats = {"projections": 0, "suggestions": 0}
    term_hash = terms.term_registry_hash(db, space_id=job.space_id)
    for viewer_account_id, root_user_id, view_id in _pfv_rows(db, space_id=job.space_id):
        edges = db.scalars(
            select(PersonalFamilyViewEdge).where(
                PersonalFamilyViewEdge.view_id == view_id,
                PersonalFamilyViewEdge.inclusion_reason_code != "inferred_path",
            )
        ).all()
        for edge in edges:
            if edge.to_user_id == root_user_id:
                continue
            raw_level = (edge.authorization_basis_json or {}).get("term_source_level")
            baseline_source = raw_level if isinstance(raw_level, str) else None
            concept = edge.concept_code
            if not concept or concept == "SELF":
                continue
            if baseline_source is not None and baseline_source not in _OVERRIDABLE_BASELINE_SOURCES:
                continue  # personal/space 显式词条：读取已无条件优先，跳过
            semantic_hash = projection_semantic_hash(
                viewer_account_id=viewer_account_id,
                space_id=job.space_id,
                target_user_id=int(edge.to_user_id),
                concept_code=concept,
                path_fact_revisions=_edge_fact_revisions(edge.path_json or []),
                term_registry_hash=term_hash,
            )
            improvement: tuple[str, str] | None = None
            if baseline_source == "derived" and edge.term:
                improvement = (edge.term, REASON_SHORTER_CHAIN)
            else:
                usage = _explicit_usage_term(
                    db,
                    account_id=viewer_account_id,
                    space_id=job.space_id,
                    concept_code=concept,
                )
                if usage is not None and edge.term and usage[0] != edge.term:
                    if not has_suppression(
                        db,
                        viewer_account_id=viewer_account_id,
                        space_id=job.space_id,
                        target_user_id=int(edge.to_user_id),
                        key=suppression_key_for(
                            viewer_account_id=viewer_account_id,
                            space_id=job.space_id,
                            target_user_id=int(edge.to_user_id),
                            concept_code=concept,
                            term=usage[0],
                        ),
                    ):
                        improvement = (usage[0], REASON_PREFERRED_USAGE)
            projection, _changed = upsert_projection(
                db,
                space_id=job.space_id,
                viewer_account_id=viewer_account_id,
                root_user_id=root_user_id,
                target_user_id=int(edge.to_user_id),
                concept_code=concept,
                semantic_hash=semantic_hash,
                baseline_term=edge.term,
                baseline_source=baseline_source,
                term=improvement[0] if improvement else None,
                origin="deterministic" if improvement else None,
                now=now,
            )
            stats["projections"] += 1
            if improvement is None:
                continue
            viewer = db.get(User, root_user_id)
            if viewer is None:
                continue
            _suggestion, created = upsert_term_preference_suggestion(
                db,
                space_id=job.space_id,
                viewer_account_id=viewer_account_id,
                subject_user_id=root_user_id,
                object_user_id=int(edge.to_user_id),
                concept_code=concept,
                term=improvement[0],
                projection_id=projection.id,
                projection_revision=projection.revision,
                semantic_hash=semantic_hash,
                reason_code=improvement[1],
                policy_version=job.policy_version,
                origin="deterministic",
                now=now,
            )
            stats["suggestions"] += int(created)
    return stats


# ---- 模型工作发现与栅栏 ----


def collect_model_groups(
    db: Session,
    *,
    space_id: int,
    max_groups: int,
    max_targets: int,
) -> list[dict[str, Any]]:
    """有界模型目标分组（每空间每 job 有限组）。

    只包含可核验改善空间的 confirmed 路径：长链（derived baseline）或存在
    可核验同义候选而 baseline 未采用。检查过的（last_checked_hash 命中当前
    语义）跳过——无新输入不重复调用。
    """
    groups: dict[int, dict[str, Any]] = {}
    term_hash = terms.term_registry_hash(db, space_id=space_id)
    for viewer_account_id, root_user_id, view_id in _pfv_rows(db, space_id=space_id):
        edges = db.scalars(
            select(PersonalFamilyViewEdge).where(
                PersonalFamilyViewEdge.view_id == view_id,
                PersonalFamilyViewEdge.inclusion_reason_code != "inferred_path",
            )
        ).all()
        for edge in edges:
            concept = edge.concept_code
            if not concept or concept == "SELF" or edge.to_user_id == root_user_id:
                continue
            raw_level = (edge.authorization_basis_json or {}).get("term_source_level")
            baseline_source = raw_level if isinstance(raw_level, str) else None
            if baseline_source is None or baseline_source not in _OVERRIDABLE_BASELINE_SOURCES:
                continue
            semantic_hash = projection_semantic_hash(
                viewer_account_id=viewer_account_id,
                space_id=space_id,
                target_user_id=int(edge.to_user_id),
                concept_code=concept,
                path_fact_revisions=_edge_fact_revisions(edge.path_json or []),
                term_registry_hash=term_hash,
            )
            request_hash = request_hash_for(semantic_hash)
            from app.models.steward import StewardTermProjection

            row = db.scalar(
                select(StewardTermProjection).where(
                    StewardTermProjection.space_id == space_id,
                    StewardTermProjection.viewer_account_id == viewer_account_id,
                    StewardTermProjection.target_user_id == int(edge.to_user_id),
                )
            )
            if row is not None and row.last_checked_hash == request_hash:
                continue  # 同输入已检查：不重新入队
            group = groups.setdefault(
                viewer_account_id,
                {
                    "viewer_account_id": viewer_account_id,
                    "root_user_id": root_user_id,
                    "targets": [],
                },
            )
            if len(group["targets"]) >= max_targets:
                continue
            group["targets"].append(
                {
                    "projection_id": int(row.id) if row is not None else None,
                    "target_user_id": int(edge.to_user_id),
                    "concept_code": concept,
                    "baseline_term": edge.term,
                    "path": edge.path_json or [],
                    "semantic_hash": semantic_hash,
                    "age_context": bool(terms._sibling_base_hop(concept.split("-")) is not None),
                }
            )
    result = []
    for account_id in sorted(groups):
        group = groups[account_id]
        targets = sorted(group["targets"], key=lambda t: (t["target_user_id"],))
        if targets:
            result.append({**group, "targets": targets[:max_targets]})
        if len(result) >= max_groups:
            break
    return result


def targets_digest(targets: list[dict[str, Any]]) -> str:
    return _canonical_hash([int(t["target_user_id"]) for t in targets])[:16]


# ---- 模型输入投影 ----


def project_terminology_input(db: Session, group: dict[str, Any]) -> str:
    """terminology user prompt：代号 + 结构 + baseline + 可核验元数据。

    不含姓名、生日原值、无关事实、其他账号数据；目标代号由服务端分配。
    """
    roster: list[dict[str, Any]] = []
    for index, target in enumerate(group["targets"]):
        ref = f"t{index + 1:03d}"
        roster.append(
            {
                "target_ref": ref,
                "concept_code": target["concept_code"],
                "baseline_term": target["baseline_term"],
                "age_order_available": bool(target.get("age_context")),
                "semantic_hash": target["semantic_hash"],
            }
        )
    import json

    return json.dumps({"version": 1, "targets": roster}, ensure_ascii=False)


def validate_model_output(
    db: Session,
    *,
    text: str,
    group: dict[str, Any],
    space_id: int,
) -> dict[str, Any] | None:
    """terminology 输出校验（封闭 schema + 服务端语义重验）。

    返回 {"items": [{target_user_id, concept_code, term, reason_code}]}；
    结构不合法返回 None（degraded）。语义不合法/被抑制/无改善的条目逐条
    丢弃，不转待批准。
    """
    import json

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    if payload.get("context_hash") is not None and not isinstance(payload.get("context_hash"), str):
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    if len(items) > len(group["targets"]):
        return None
    seen_refs: set[str] = set()
    accepted: list[dict[str, Any]] = []
    term_hash = terms.term_registry_hash(db, space_id=space_id)
    for item in items:
        if not isinstance(item, dict):
            return None
        if set(item.keys()) - {"target_ref", "concept_code", "term", "reason_code"}:
            return None
        ref = item.get("target_ref")
        if not isinstance(ref, str) or ref in seen_refs:
            return None
        seen_refs.add(ref)
        index = _ref_index(ref)
        if index is None or index >= len(group["targets"]):
            continue  # 未知目标：丢弃该条
        target = group["targets"][index]
        term_text = item.get("term")
        reason = item.get("reason_code")
        if reason not in (REASON_SYNONYM, REASON_SHORTER_CHAIN, REASON_PREFERRED_USAGE):
            continue
        if not isinstance(term_text, str):
            continue
        term_text = term_text.strip()
        if not term_text or len(term_text) > TERM_MAX_LENGTH:
            continue
        if term_text == target["baseline_term"]:
            continue  # 无改善
        if item.get("concept_code") != target["concept_code"]:
            continue  # 不信任模型自报概念码：必须与服务端真值一致
        variant_context = None
        from app.services.relationship_graph import load_birth_years

        if target.get("age_context"):
            persons = {int(t["target_user_id"]) for t in group["targets"]}
            for step in target["path"]:
                persons.add(int(step["from"]))
                persons.add(int(step["to"]))
            births = load_birth_years(
                db,
                viewer_user_id=int(group["root_user_id"]),
                space_id=space_id,
                user_ids=persons,
            )
            variant_context = terms.VariantContext(
                viewer_user_id=int(group["root_user_id"]),
                path=target["path"],
                births=births,
            )
        if not term_semantics_valid(
            db,
            account_id=int(group["viewer_account_id"]),
            space_id=space_id,
            concept_code=target["concept_code"],
            term=term_text,
            variant_context=variant_context,
        ):
            continue
        key = suppression_key_for(
            viewer_account_id=int(group["viewer_account_id"]),
            space_id=space_id,
            target_user_id=int(target["target_user_id"]),
            concept_code=target["concept_code"],
            term=term_text,
        )
        if has_suppression(
            db,
            viewer_account_id=int(group["viewer_account_id"]),
            space_id=space_id,
            target_user_id=int(target["target_user_id"]),
            key=key,
        ):
            continue
        accepted.append(
            {
                "target_user_id": int(target["target_user_id"]),
                "concept_code": target["concept_code"],
                "term": term_text,
                "reason_code": reason,
                "semantic_hash": projection_semantic_hash(
                    viewer_account_id=int(group["viewer_account_id"]),
                    space_id=space_id,
                    target_user_id=int(target["target_user_id"]),
                    concept_code=target["concept_code"],
                    path_fact_revisions=_edge_fact_revisions(target["path"]),
                    term_registry_hash=term_hash,
                ),
            }
        )
    return {"items": accepted}


def _ref_index(ref: str) -> int | None:
    if not ref.startswith("t") or not ref[1:].isdigit():
        return None
    return int(ref[1:]) - 1


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
    if row is None or row.term is None:
        return None
    if row.baseline_term is not None and row.baseline_term != baseline_term:
        # baseline 漂移：旧自动词失效，回退当前 baseline
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
