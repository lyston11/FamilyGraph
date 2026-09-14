"""管家称谓自主优化（任务 09-13-steward-terminology-autonomy）。

职责（B design.md）：
- **生产发现**（发布后事务外准备，零模型）：对当前 PFV 已确认路径计算
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
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.personal_family_view import PersonalFamilyView, PersonalFamilyViewEdge
from app.models.space import SpaceMember
from app.models.steward import (
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardTermProjection,
    StewardTermSuppression,
    StewardViewTarget,
)
from app.models.steward_suggestion import StewardSuggestion
from app.models.term_registry import TermEntry, TermUsage
from app.models.user import User
from app.services import steward_snapshot, steward_terminology_snapshot, terms
from app.services.steward_snapshot import SnapshotChanged, ViewerInput
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
    """Use exactly the detached vocabulary consumed by generation presentation."""
    return steward_terminology_snapshot.allowed_terms(
        terms.load_term_snapshot(db, account_id=account_id, space_id=space_id),
        concept_code=concept_code,
        variant_context=variant_context,
    )


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


# ---- Published, authorized target inputs ----


def _confirmed_targets(
    db: Session,
    *,
    space_id: int,
    generation_id: int | None = None,
    meaningful_only: bool = False,
) -> list[dict[str, Any]]:
    """Read immutable published targets; legacy rows are used only before staging.

    Planning may name a still-staged generation. It never loads graphs and the
    coordinator fences its detached intents before making them deliverable.
    """
    if generation_id is None:
        publication = db.get(StewardPublication, space_id, populate_existing=True)
        if publication is not None:
            generation = db.get(
                StewardGeneration, publication.generation_id, populate_existing=True
            )
            if generation is None or not valid_delivery_generation(db, generation):
                return []
            generation_id = generation.id
    if generation_id is not None:
        view = StewardGenerationView
        target = StewardViewTarget
        concept = target.edge_json["concept_code"].as_string()
        stmt = (
            select(
                view.viewer_account_id,
                view.root_user_id,
                target.target_user_id,
                target.edge_json["path"],
                concept,
                target.edge_json["term"].as_string(),
                target.edge_json["term_source_level"].as_string(),
            )
            .select_from(view)
            .join(target, target.view_id == func.coalesce(view.result_view_id, view.id))
            .join(Account, Account.id == view.viewer_account_id)
            .join(
                SpaceMember,
                (SpaceMember.user_id == Account.user_id) & (SpaceMember.space_id == view.space_id),
            )
            .where(
                view.generation_id == generation_id,
                view.space_id == space_id,
                view.status == "ready",
                view.root_user_id == Account.user_id,
                SpaceMember.status == "active",
                target.status == "ready",
                target.edge_json["inclusion_reason_code"].as_string() == "confirmed_path",
            )
        )
        if meaningful_only:
            usage = (
                select(TermUsage.id)
                .join(TermEntry, TermEntry.id == TermUsage.term_entry_id)
                .where(
                    TermUsage.account_id == view.viewer_account_id,
                    TermUsage.space_id == space_id,
                    TermEntry.concept_code == concept,
                )
                .exists()
            )
            projection = (
                select(StewardTermProjection.id)
                .where(
                    StewardTermProjection.viewer_account_id == view.viewer_account_id,
                    StewardTermProjection.root_user_id == view.root_user_id,
                    StewardTermProjection.space_id == space_id,
                    StewardTermProjection.target_user_id == target.target_user_id,
                    StewardTermProjection.term.is_not(None),
                )
                .exists()
            )
            suppression = (
                select(StewardTermSuppression.id)
                .where(
                    StewardTermSuppression.viewer_account_id == view.viewer_account_id,
                    StewardTermSuppression.space_id == space_id,
                    StewardTermSuppression.target_user_id == target.target_user_id,
                )
                .exists()
            )
            stmt = stmt.where(
                or_(
                    target.edge_json["term_source_level"].as_string() == "derived",
                    usage,
                    projection,
                    suppression,
                )
            )
        rows = db.execute(stmt.order_by(view.viewer_account_id, target.target_user_id)).all()
    else:
        legacy = PersonalFamilyView
        edge = PersonalFamilyViewEdge
        rows = db.execute(
            select(
                legacy.viewer_account_id,
                legacy.root_user_id,
                edge.to_user_id,
                edge.path_json,
                edge.concept_code,
                edge.term,
                edge.authorization_basis_json["term_source_level"].as_string(),
            )
            .select_from(legacy)
            .join(edge, edge.view_id == legacy.id)
            .join(Account, Account.id == legacy.viewer_account_id)
            .join(
                SpaceMember,
                (SpaceMember.user_id == Account.user_id)
                & (SpaceMember.space_id == legacy.space_id),
            )
            .where(
                legacy.space_id == space_id,
                legacy.root_user_id == Account.user_id,
                legacy.status == "current",
                SpaceMember.status == "active",
                edge.inclusion_reason_code == "confirmed_path",
            )
            .order_by(legacy.viewer_account_id, edge.to_user_id)
        ).all()
    return [
        {
            "viewer_account_id": int(account_id),
            "root_user_id": int(root),
            "target_user_id": int(target_id),
            "path": path,
            "concept_code": concept_code,
            "term": term,
            "baseline_source": source,
        }
        for account_id, root, target_id, path, concept_code, term, source in rows
        if path
    ]


def _candidate_terms(
    db: Session,
    *,
    account_id: int,
    space_id: int,
    concept_code: str,
    variant_context: terms.VariantContext | None = None,
) -> set[str]:
    return steward_terminology_snapshot.candidate_terms(
        terms.load_term_snapshot(db, account_id=account_id, space_id=space_id),
        concept_code=concept_code,
        variant_context=variant_context,
    )


def current_target_context(
    db: Session,
    *,
    viewer_account_id: int,
    root_user_id: int,
    space_id: int,
    target_user_id: int,
    path: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Compatibility reader sharing the pipeline's authorized, pure semantics."""
    if root_user_id == target_user_id:
        return None
    try:
        snapshot = steward_snapshot.viewer_from_session(
            db, space_id=space_id, account_id=viewer_account_id
        )
    except SnapshotChanged:
        return None
    if snapshot.root_user_id != root_user_id:
        return None
    return steward_terminology_snapshot.current_target_context(
        snapshot, target_user_id=target_user_id, path=path
    )


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


TERMINOLOGY_TARGET_BATCH_SIZE = 8
_DELIVERY_SNAPSHOT_CACHE_SIZE = 16


@dataclass(frozen=True)
class _DeliverySnapshot:
    snapshot: ViewerInput
    versions: dict[str, Any]
    valid_until: datetime
    generation_created_at: datetime


# Only production baseline calculation uses this cache, never effective display.
# Engine identity prevents isolated databases with reused IDs/revisions colliding.
_DELIVERY_SNAPSHOTS: OrderedDict[tuple[Engine, int, int], _DeliverySnapshot] = OrderedDict()
_DELIVERY_SNAPSHOT_LOCK = RLock()


def valid_delivery_generation(db: Session, generation: StewardGeneration) -> bool:
    """Own terminology writes may change presentation, never structure or policy."""
    if (
        generation.status != "published"
        or not generation.manifest_sealed
        or generation.valid_until is None
        or generation.valid_until <= utcnow()
        or db.scalar(
            select(StewardPublication.generation_id).where(
                StewardPublication.space_id == generation.space_id
            )
        )
        != generation.id
    ):
        return False
    expected = generation.input_versions_json
    current = steward_snapshot.input_versions(db, generation.space_id)
    return bool(
        expected.get("version") == current["version"]
        and expected.get("config") == current["config"]
        and expected.get("global", [])[:1] == current["global"][:1]
        and expected.get("space", [])[:1] == current["space"][:1]
    )


def delivery_items_for_generation(db: Session, *, generation_id: int) -> list[dict[str, Any]]:
    """Prepare bounded, useful local work from a generation's confirmed targets."""
    generation = db.get(StewardGeneration, generation_id)
    if generation is None:
        return []
    items: list[dict[str, Any]] = []
    for target in _confirmed_targets(
        db,
        space_id=generation.space_id,
        generation_id=generation_id,
        meaningful_only=True,
    ):
        if (
            not items
            or items[-1]["viewer_account_id"] != target["viewer_account_id"]
            or len(items[-1]["targets"]) >= TERMINOLOGY_TARGET_BATCH_SIZE
        ):
            items.append(
                {
                    "generation_id": generation_id,
                    "viewer_account_id": target["viewer_account_id"],
                    "root_user_id": target["root_user_id"],
                    "targets": [],
                }
            )
        items[-1]["targets"].append(
            {"target_user_id": target["target_user_id"], "path": target["path"]}
        )
    return items


def _delivery_cache_key(
    bind: Engine | Connection, *, generation_id: int, viewer_account_id: int
) -> tuple[Engine, int, int]:
    return (bind.engine if isinstance(bind, Connection) else bind, generation_id, viewer_account_id)


def _cache_snapshot(key: tuple[Engine, int, int], entry: _DeliverySnapshot) -> None:
    with _DELIVERY_SNAPSHOT_LOCK:
        _DELIVERY_SNAPSHOTS[key] = entry
        _DELIVERY_SNAPSHOTS.move_to_end(key)
        while len(_DELIVERY_SNAPSHOTS) > _DELIVERY_SNAPSHOT_CACHE_SIZE:
            _DELIVERY_SNAPSHOTS.popitem(last=False)


def _cached_snapshot(key: tuple[Engine, int, int]) -> _DeliverySnapshot | None:
    # Immediate delivery and maintenance can drain concurrently. Never hold this
    # bookkeeping lock while reading SQLite or calculating a target.
    with _DELIVERY_SNAPSHOT_LOCK:
        cached = _DELIVERY_SNAPSHOTS.get(key)
        if cached is not None:
            _DELIVERY_SNAPSHOTS.move_to_end(key)
        return cached


def prepare_delivery_item(
    bind: Engine | Connection, *, item: dict[str, Any]
) -> dict[str, Any] | None:
    """Copy one consistent authorized snapshot, close it, then calculate the batch."""
    generation_id = int(item["generation_id"])
    account_id, root = int(item["viewer_account_id"]), int(item["root_user_id"])
    targets = item["targets"]
    if not isinstance(targets, list) or not 0 < len(targets) <= TERMINOLOGY_TARGET_BATCH_SIZE:
        raise ValueError("invalid terminology target batch")
    key = _delivery_cache_key(bind, generation_id=generation_id, viewer_account_id=account_id)
    with steward_snapshot.read_transaction(bind) as db:
        generation = db.get(StewardGeneration, generation_id)
        if generation is None or not valid_delivery_generation(db, generation):
            raise SnapshotChanged
        space_id = generation.space_id
        versions = steward_snapshot.input_versions(db, space_id)
        cached = _cached_snapshot(key)
        if (
            cached is not None
            and cached.versions == versions
            and cached.valid_until > utcnow()
            and cached.snapshot.root_user_id == root
            and cached.generation_created_at == generation.created_at
        ):
            snapshot = cached.snapshot
        else:
            snapshot = steward_snapshot.viewer_from_session(
                db, space_id=space_id, account_id=account_id
            )
            if snapshot.root_user_id != root:
                raise SnapshotChanged
            assert generation.valid_until is not None
            _cache_snapshot(
                key,
                _DeliverySnapshot(
                    snapshot, versions, generation.valid_until, generation.created_at
                ),
            )
        valid_until = generation.valid_until
        generation_created_at = generation.created_at
    # No Session, lazy ORM attribute, graph search or term calculation below
    # participates in a database transaction.
    contexts = [
        context
        for target in targets
        if (
            context := steward_terminology_snapshot.current_target_context(
                snapshot,
                target_user_id=int(target["target_user_id"]),
                path=target["path"],
            )
        )
        is not None
    ]
    return {
        "generation_id": generation_id,
        "generation_created_at": generation_created_at,
        "space_id": space_id,
        "viewer_account_id": account_id,
        "root_user_id": root,
        "input_versions": versions,
        "valid_until": valid_until,
        "targets": contexts,
    }


def _apply_target(
    db: Session,
    *,
    job: StewardJob,
    viewer_account_id: int,
    root_user_id: int,
    target: dict[str, Any],
    now: Any,
) -> dict[str, Any]:
    baseline_source = target["baseline_source"]
    if baseline_source not in _OVERRIDABLE_BASELINE_SOURCES:
        return {"projections": 0, "suggestions": 0, "changed": False}
    baseline = target["baseline_term"]
    preferred = target["preferred_term"]
    override = preferred if preferred and preferred != baseline else None
    suggestion_term = override or (baseline if baseline_source == "derived" else None)
    previous = db.scalar(
        select(StewardTermProjection).where(
            StewardTermProjection.space_id == job.space_id,
            StewardTermProjection.viewer_account_id == viewer_account_id,
            StewardTermProjection.root_user_id == root_user_id,
            StewardTermProjection.target_user_id == target["target_user_id"],
        )
    )
    # A baseline-only locale/system/structural row changes nothing. Model discovery
    # operates from published targets and can create its projection when needed.
    if previous is None and override is None and suggestion_term is None:
        return {"projections": 0, "suggestions": 0, "changed": False}
    had_override = previous is not None and previous.term is not None
    projection, changed = upsert_projection(
        db,
        space_id=job.space_id,
        viewer_account_id=viewer_account_id,
        root_user_id=root_user_id,
        target_user_id=int(target["target_user_id"]),
        concept_code=target["concept_code"],
        semantic_hash=target["semantic_hash"],
        baseline_term=baseline,
        baseline_source=baseline_source,
        term=override,
        origin="deterministic" if override else None,
        now=now,
    )
    created = False
    if suggestion_term is not None:
        _suggestion, created = upsert_term_preference_suggestion(
            db,
            space_id=job.space_id,
            viewer_account_id=viewer_account_id,
            subject_user_id=root_user_id,
            object_user_id=int(target["target_user_id"]),
            concept_code=target["concept_code"],
            term=suggestion_term,
            projection_id=projection.id,
            projection_revision=projection.revision,
            semantic_hash=target["semantic_hash"],
            reason_code=REASON_PREFERRED_USAGE if override else REASON_SHORTER_CHAIN,
            policy_version=job.policy_version,
            origin="deterministic",
            now=now,
        )
    return {
        "projections": 1,
        "suggestions": int(created),
        "changed": bool(changed and (had_override or override is not None)),
    }


def apply_delivery_item(
    db: Session, *, job: StewardJob, prepared: dict[str, Any]
) -> dict[str, Any]:
    """Fence the whole batch once, then atomically upsert at most eight targets."""
    account_id, root = int(prepared["viewer_account_id"]), int(prepared["root_user_id"])
    generation = db.get(StewardGeneration, int(prepared["generation_id"]))
    if (
        len(prepared["targets"]) > TERMINOLOGY_TARGET_BATCH_SIZE
        or generation is None
        or generation.job_id != job.id
        or prepared["space_id"] != job.space_id
        or not valid_delivery_generation(db, generation)
        or prepared["valid_until"] is None
        or prepared["valid_until"] <= utcnow()
        or steward_snapshot.input_versions(db, job.space_id) != prepared["input_versions"]
        or db.scalar(
            select(Account.id)
            .join(User, User.id == Account.user_id)
            .join(
                SpaceMember,
                (SpaceMember.user_id == Account.user_id) & (SpaceMember.space_id == job.space_id),
            )
            .where(
                Account.id == account_id,
                Account.user_id == root,
                User.deleted_at.is_(None),
                SpaceMember.status == "active",
            )
        )
        is None
    ):
        raise SnapshotChanged
    result: dict[str, Any] = {
        "viewer_account_id": account_id,
        "projections": 0,
        "suggestions": 0,
        "changed": False,
    }
    now = utcnow()
    for target in prepared["targets"]:
        applied = _apply_target(
            db,
            job=job,
            viewer_account_id=account_id,
            root_user_id=root,
            target=target,
            now=now,
        )
        result["projections"] += applied["projections"]
        result["suggestions"] += applied["suggestions"]
        result["changed"] = result["changed"] or applied["changed"]
    db.flush()
    # The caller confirms only after the intent receipt and this write commit.
    # Advancing a cache before commit could accept a rollback/revision ABA.
    prepared["_applied_versions"] = steward_snapshot.input_versions(db, job.space_id)
    return result


def confirm_delivery_item(bind: Engine | Connection, *, prepared: dict[str, Any]) -> None:
    """Accept only a successfully committed batch's own presentation increments."""
    versions = prepared.get("_applied_versions")
    if versions is None:
        return
    key = _delivery_cache_key(
        bind,
        generation_id=int(prepared["generation_id"]),
        viewer_account_id=int(prepared["viewer_account_id"]),
    )
    with _DELIVERY_SNAPSHOT_LOCK:
        cached = _DELIVERY_SNAPSHOTS.get(key)
        if (
            cached is not None
            and cached.versions == prepared["input_versions"]
            and cached.generation_created_at == prepared["generation_created_at"]
        ):
            _cache_snapshot(
                key,
                _DeliverySnapshot(
                    cached.snapshot, versions, cached.valid_until, cached.generation_created_at
                ),
            )


def run_deterministic_scan(db: Session, *, job: StewardJob, now: Any = None) -> dict[str, int]:
    """Legacy helper; production uses detached prepare/apply delivery batches."""
    now = now or utcnow()
    stats = {"projections": 0, "suggestions": 0}
    changed_viewers: set[int] = set()
    snapshot: ViewerInput | None = None
    for row in _confirmed_targets(db, space_id=job.space_id):
        if snapshot is None or snapshot.account_id != row["viewer_account_id"]:
            try:
                snapshot = steward_snapshot.viewer_from_session(
                    db, space_id=job.space_id, account_id=row["viewer_account_id"]
                )
            except SnapshotChanged:
                snapshot = None
                continue
        target = steward_terminology_snapshot.current_target_context(
            snapshot, target_user_id=row["target_user_id"], path=row["path"]
        )
        if target is None:
            continue
        result = _apply_target(
            db,
            job=job,
            viewer_account_id=row["viewer_account_id"],
            root_user_id=row["root_user_id"],
            target=target,
            now=now,
        )
        stats["projections"] += result["projections"]
        stats["suggestions"] += result["suggestions"]
        if result["changed"]:
            changed_viewers.add(row["viewer_account_id"])
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
    """Find bounded model groups without requiring empty baseline projections.

    Cheap persisted metadata/vocabulary filtering precedes any graph load. One
    detached viewer serves every selected target; completed groups stop discovery.
    Last-attempt ordering preserves the existing fair rotation.
    """
    if max_groups <= 0 or max_targets <= 0:
        return []
    from app.services.steward_assist import terminology_target_retryable

    by_viewer: dict[int, list[dict[str, Any]]] = {}
    for row in _confirmed_targets(db, space_id=space_id):
        if row["baseline_source"] in (*_OVERRIDABLE_BASELINE_SOURCES, terms.SOURCE_LEVEL_STEWARD):
            by_viewer.setdefault(row["viewer_account_id"], []).append(row)
    projections = {
        (row.viewer_account_id, row.root_user_id, row.target_user_id): row
        for row in db.scalars(
            select(StewardTermProjection).where(StewardTermProjection.space_id == space_id)
        )
    }

    def attempt_order(row: dict[str, Any]) -> tuple[str, int]:
        previous = projections.get(
            (row["viewer_account_id"], row["root_user_id"], row["target_user_id"])
        )
        attempted = (
            previous.last_attempt_at.isoformat() if previous and previous.last_attempt_at else ""
        )
        return attempted, int(row["target_user_id"])

    ordered = sorted(
        by_viewer.items(),
        key=lambda pair: (min(attempt_order(row)[0] for row in pair[1]), pair[0]),
    )
    groups: list[dict[str, Any]] = []
    for account_id, rows in ordered:
        registry = terms.load_term_snapshot(db, account_id=account_id, space_id=space_id)
        snapshot: ViewerInput | None = None
        targets: list[dict[str, Any]] = []
        for row in sorted(rows, key=attempt_order):
            concept = row["concept_code"]
            if not concept or concept == "SELF":
                continue
            previous = projections.get((account_id, row["root_user_id"], row["target_user_id"]))
            baseline_hint = (
                previous.baseline_term
                if previous is not None and previous.baseline_term is not None
                else row["term"]
            ) or ""
            allowed_hint = steward_terminology_snapshot.candidate_terms(
                registry, concept_code=concept
            )
            if (
                not any(t != baseline_hint and len(t) <= len(baseline_hint) for t in allowed_hint)
                and terms.sibling_base_hop(concept.split("-")) is None
            ):
                continue
            if snapshot is None:
                try:
                    snapshot = steward_snapshot.viewer_from_session(
                        db, space_id=space_id, account_id=account_id
                    )
                except SnapshotChanged:
                    break
            target = steward_terminology_snapshot.current_target_context(
                snapshot, target_user_id=row["target_user_id"], path=row["path"]
            )
            if target is None or target["baseline_source"] not in _OVERRIDABLE_BASELINE_SOURCES:
                continue
            baseline = target["baseline_term"] or ""
            if target["preferred_term"] and target["preferred_term"] != baseline:
                continue
            if not any(t != baseline and len(t) <= len(baseline) for t in target["allowed_terms"]):
                continue
            if previous is not None and previous.last_checked_hash == target["request_hash"]:
                continue
            if not terminology_target_retryable(
                db,
                space_id=space_id,
                viewer_account_id=account_id,
                root_user_id=snapshot.root_user_id,
                target_user_id=row["target_user_id"],
                semantic_hash=target["semantic_hash"],
                request_hash=target["request_hash"],
            ):
                continue
            target["projection_id"] = previous.id if previous is not None else None
            targets.append(target)
            if len(targets) >= max_targets:
                break
        if targets:
            assert snapshot is not None
            groups.append(
                {
                    "viewer_account_id": account_id,
                    "root_user_id": snapshot.root_user_id,
                    "space_id": space_id,
                    "targets": targets,
                }
            )
            if len(groups) >= max_groups:
                break
    return groups


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
    """Read-only adapter to the same selector used by staged PFV presentation."""
    if concept_code is None or concept_code == "SELF":
        return None
    if baseline_source is not None and baseline_source not in _OVERRIDABLE_BASELINE_SOURCES:
        return None
    if (
        db.scalar(
            select(StewardTermProjection.id).where(
                StewardTermProjection.space_id == space_id,
                StewardTermProjection.viewer_account_id == account_id,
                StewardTermProjection.root_user_id == root_user_id,
                StewardTermProjection.target_user_id == target_user_id,
                StewardTermProjection.status == "active",
                StewardTermProjection.term.is_not(None),
                StewardTermProjection.rule_version == RULE_VERSION,
            )
        )
        is None
    ):
        return None
    try:
        snapshot = steward_snapshot.viewer_from_session(
            db, space_id=space_id, account_id=account_id
        )
    except SnapshotChanged:
        return None
    if snapshot.root_user_id != root_user_id:
        return None
    return steward_terminology_snapshot.effective_override(
        snapshot,
        target_user_id=target_user_id,
        concept_code=concept_code,
        baseline_term=baseline_term,
        baseline_source=baseline_source,
        path=path,
    )


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
