"""V2.5 memory, scoped retrieval and context projection services.

The service has one important invariant: an AgentMessage can be a candidate
source, but it can never be ingested directly. Retrieval starts with a SQL
scope predicate and applies VisibilityPolicy once more before returning text.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, and_, bindparam, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    MEMORY_CANDIDATE_NOT_FOUND,
    MEMORY_DISABLED,
    MEMORY_SCOPE_FORBIDDEN,
    MEMORY_SENSITIVE_SCOPE_FORBIDDEN,
    MEMORY_STATE_CONFLICT,
    POLICY_CONTEXT_INVALID,
    PROVIDER_LOCAL_REQUIRED_UNAVAILABLE,
    RAG_DISABLED,
    RAG_SOURCE_NOT_ALLOWED,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import AgentRun
from app.models.context import ContextBuild, ContextBuildItem
from app.models.memory import (
    MEMORY_SCOPES,
    MEMORY_SUPERSEDE_REASONS,
    SENSITIVITY_LEVELS,
    Memory,
    MemoryCandidate,
)
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAG_SOURCE_TYPES, RAGChunk, RAGDocument
from app.models.space import FamilySpace
from app.models.user import User
from app.services import memory_sources, platform_features, rag_search_provider
from app.services.agent_provider import ProviderResolution, resolve_for_space
from app.services.domain_events import emit as emit_domain_event
from app.services.policy_consumer import is_policy_consumer_kind
from app.services.rag_budget import estimate_tokens
from app.services.rag_query import QueryPlan, plan_query
from app.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

# v2: deterministic sentence/paragraph chunking with bounded overlap.  The
# chunk algorithm version is part of chunk identity; a switch materializes a
# new document version instead of silently re-pointing old handles.
RAG_INDEX_VERSION = "fts5-trigram-v2"
_CHUNK_MAX_CHARS = 800
_CHUNK_OVERLAP_CHARS = 100
_CANDIDATE_TEXT_LIMIT = 12_000
_SHARED_SCOPES = ("household", "lineage")


def _require_memory_enabled(db: Session) -> None:
    if not platform_features.is_memory_enabled(db):
        raise_api_error(503, MEMORY_DISABLED, "Memory 功能未开启")


def _require_rag_enabled(db: Session) -> None:
    if not platform_features.is_rag_enabled(db):
        raise_api_error(503, RAG_DISABLED, "RAG 功能未开启")


@dataclass(frozen=True)
class MemoryCandidateInput:
    source_quote: str
    summary: str
    suggested_scope: str
    purpose: str
    sensitivity: str = "normal"
    source_message_id: int | None = None
    source_document_ref: str | None = None
    # 提取器类别标签（规则式 detector 提供）；仅用于幂等键稳定化，不入库字段。
    extractor_category: str | None = None


@dataclass(frozen=True)
class RAGHit:
    document_id: int
    chunk_id: int
    source_id: str
    text: str
    token_estimate: int
    rank: float
    citation_handle: str
    scope: str
    sensitivity: str
    revision: int
    source_type: str = "memory"
    index_version: str = RAG_INDEX_VERSION
    space_id: int | None = None
    allowed_scopes: tuple[str, ...] = ()
    chunk_index: int | None = None
    source_revision: int | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class ContextBlock:
    source_id: str
    text: str
    token_estimate: int
    citation_handle: str
    trust: str = "untrusted_data"


@dataclass(frozen=True)
class ContextProjection:
    build_id: int
    blocks: tuple[ContextBlock, ...]
    provider: ProviderResolution
    local_required: bool


class MemoryCandidateExtractor:
    """Small, deterministic candidate extractor seam; never indexes input text."""

    version = "memory-extractor-v1"

    def __init__(self, detector: Callable[[str], list[MemoryCandidateInput]] | None = None):
        self._detector = detector or self._default_detector

    def extract(
        self,
        db: Session,
        *,
        author_account_id: int,
        conversation_text: str,
        source_message_id: int | None = None,
    ) -> list[MemoryCandidate]:
        rows: list[MemoryCandidate] = []
        for index, item in enumerate(self._detector(conversation_text)):
            resolved_message_id = item.source_message_id or source_message_id
            label = item.extractor_category or str(index)
            rows.append(
                propose_candidate(
                    db,
                    author_account_id=author_account_id,
                    source_message_id=resolved_message_id,
                    source_document_ref=item.source_document_ref,
                    source_quote=item.source_quote,
                    summary=item.summary,
                    suggested_scope=item.suggested_scope,
                    purpose=item.purpose,
                    sensitivity=item.sensitivity,
                    extractor_version=self.version,
                    idempotency_key=f"extract:{resolved_message_id}:{label}:{index}",
                )
            )
        return rows

    @staticmethod
    def _default_detector(text_value: str) -> list[MemoryCandidateInput]:
        """Delegate to the deterministic rule extractor (2026-09-15 audit fix).

        Lazy import avoids a module cycle: memory_extractor imports
        MemoryCandidateInput/propose_candidate from this module. Ordinary chat
        still never becomes durable memory without the user's confirmation —
        this only proposes review cards.
        """
        from app.services.memory_extractor import rule_detector

        return list(rule_detector(text_value))


def _validate_sensitivity(value: str) -> None:
    if value not in SENSITIVITY_LEVELS:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "敏感等级不合法", {"sensitivity": value})


def _validate_scope(scope: str, space_id: int | None) -> None:
    if scope not in MEMORY_SCOPES:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆 scope 不合法", {"scope": scope})
    if (scope == "private") != (space_id is None):
        raise_api_error(422, MEMORY_STATE_CONFLICT, "private 不得绑定空间，shared 必须绑定空间")


def _parse_memory_scope(scope: str, space_id: int | None) -> tuple[str, int | None]:
    """Accept the API spelling ``household:<space>`` while storing normalized scope."""
    if ":" not in scope:
        return scope, space_id
    kind, raw_space_id = scope.split(":", 1)
    if kind not in _SHARED_SCOPES or not raw_space_id.isdigit() or int(raw_space_id) <= 0:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆 scope 不合法", {"scope": scope})
    parsed_space_id = int(raw_space_id)
    if space_id is not None and space_id != parsed_space_id:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "scope 中的空间与 space_id 不一致")
    return kind, parsed_space_id


def _validate_rag_scope(scope: str, space_id: int | None) -> None:
    if scope not in (*MEMORY_SCOPES, "public"):
        raise_api_error(422, MEMORY_STATE_CONFLICT, "RAG scope 不合法", {"scope": scope})
    if scope == "public":
        if space_id is not None:
            raise_api_error(422, MEMORY_STATE_CONFLICT, "public RAG 文档不得绑定空间")
        return
    _validate_scope(scope, space_id)


def propose_candidate(
    db: Session,
    *,
    author_account_id: int,
    source_quote: str | None,
    summary: str,
    suggested_scope: str,
    purpose: str,
    sensitivity: str = "normal",
    source_message_id: int | None = None,
    source_document_ref: str | None = None,
    extractor_version: str = "manual-v1",
    source: dict[str, Any] | None = None,
    source_span: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> MemoryCandidate:
    """Persist a review card only; no RAG document is created here."""
    _require_memory_enabled(db)
    if not summary.strip() or len(summary) > 20_000 or not purpose.strip() or len(purpose) > 120:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆摘要或用途为空或超长")
    if suggested_scope not in MEMORY_SCOPES:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "建议 scope 不合法", {"scope": suggested_scope})
    _validate_sensitivity(sensitivity)
    if idempotency_key is not None and (
        not idempotency_key.strip() or not 8 <= len(idempotency_key) <= 128
    ):
        raise_api_error(422, MEMORY_STATE_CONFLICT, "请求重试键不合法")
    normalized = memory_sources.normalize_source(
        source,
        source_message_id=source_message_id,
        source_document_ref=source_document_ref,
        source_span=source_span,
    )
    account = db.get(Account, author_account_id)
    if account is None:
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "记忆主体不存在")
    fingerprint = _request_fingerprint(
        {
            "source": normalized,
            "raw_quote": source_quote,
            "summary": summary.strip(),
            "suggested_scope": suggested_scope,
            "purpose": purpose.strip(),
            "sensitivity": sensitivity,
            "extractor_version": extractor_version,
        }
    )
    if idempotency_key is not None:
        existing = db.scalar(
            select(MemoryCandidate)
            .where(
                MemoryCandidate.author_account_id == author_account_id,
                MemoryCandidate.idempotency_key == idempotency_key,
            )
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            return _replay_candidate(db, existing, account, fingerprint)
    resolved = memory_sources.resolve_source(
        db, account=account, source=normalized, raw_quote=source_quote, sensitivity=sensitivity
    )
    if len(resolved.quote) > _CANDIDATE_TEXT_LIMIT:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆候选原文超长")
    now = utcnow()
    values = dict(
        author_account_id=author_account_id,
        source_message_id=resolved.message_id,
        source_document_ref=resolved.document_ref,
        source_span_json=resolved.snapshot,
        source_kind=resolved.kind,
        source_verification="verified",
        source_type=resolved.source_type,
        source_id=resolved.source_id,
        source_revision=resolved.revision,
        source_space_id=resolved.space_id,
        source_quote=resolved.quote,
        summary=summary.strip(),
        suggested_scope=suggested_scope,
        purpose=purpose.strip(),
        sensitivity=sensitivity,
        extractor_version=extractor_version,
        status="pending",
        created_at=now,
        updated_at=now,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    if idempotency_key is None:
        row = MemoryCandidate(**values)
        db.add(row)
        db.flush()
    else:
        inserted_id = db.scalar(
            sqlite_insert(MemoryCandidate)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["author_account_id", "idempotency_key"],
            )
            .returning(MemoryCandidate.id)
        )
        if inserted_id is None:
            existing = db.scalar(
                select(MemoryCandidate)
                .where(
                    MemoryCandidate.author_account_id == author_account_id,
                    MemoryCandidate.idempotency_key == idempotency_key,
                )
                .execution_options(populate_existing=True)
            )
            assert existing is not None
            return _replay_candidate(db, existing, account, fingerprint)
        loaded = db.get(MemoryCandidate, inserted_id)
        assert loaded is not None
        row = loaded
    db.get(PlatformFeatureConfig, 1, populate_existing=True)
    _require_memory_enabled(db)
    actor = db.get(User, account.user_id)
    if (
        actor is None
        or not memory_sources.source_access(db, row, actor=actor, account=account).readable
    ):
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "原来源已失效或当前无权读取")
    emit_domain_event(
        db,
        event_type="memory.candidate.proposed",
        aggregate_type="memory_candidate",
        aggregate_id=row.id,
        payload={
            "source_kind": row.source_kind,
            "source_type": row.source_type,
        },
        actor_account_id=author_account_id,
    )
    return row


def _request_fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _replay_candidate(
    db: Session, candidate: MemoryCandidate, account: Account, fingerprint: str
) -> MemoryCandidate:
    if candidate.request_fingerprint != fingerprint:
        raise_api_error(409, MEMORY_STATE_CONFLICT, "重试键已用于不同的记忆请求")
    actor = db.get(User, account.user_id)
    if (
        actor is None
        or not memory_sources.source_access(db, candidate, actor=actor, account=account).readable
    ):
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "原来源已失效或当前无权读取")
    return candidate


def create_candidate(
    db: Session,
    *,
    account: Account,
    source_span: dict[str, Any],
    raw_quote: str | None,
    summary: str,
    suggested_scope: str,
    purpose: str,
    sensitivity: str = "normal",
    source_message_id: int | None = None,
    source_document_ref: str | None = None,
    source: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> MemoryCandidate:
    return propose_candidate(
        db,
        author_account_id=account.id,
        source_quote=raw_quote,
        summary=summary,
        suggested_scope=suggested_scope,
        purpose=purpose,
        sensitivity=sensitivity,
        source_message_id=source_message_id,
        source_document_ref=source_document_ref,
        source=source,
        source_span=source_span,
        idempotency_key=idempotency_key,
    )


def _active_space_member(db: Session, *, user_id: int, space_id: int) -> bool:
    return memory_sources.active_member(db, user_id, space_id)


def _resolve_supersede_targets(
    db: Session, *, account_id: int, supersedes: Sequence[int]
) -> list[Memory]:
    """Load and validate explicit supersede targets before any state change.

    只在**显式**取代时才有目标（P1）。模型提议取代（P2）也会走这条路径，
    但必须由用户在确认请求里点名；不存在自动取代。

    「无法判定时不静默择一」在这里体现为：只校验目标是否合法，绝不去猜
    「哪条旧记忆应该被取代」——猜错就是静默丢事实。
    """
    targets: list[Memory] = []
    seen: set[int] = set()
    for raw in supersedes:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise_api_error(422, MEMORY_STATE_CONFLICT, "取代目标不合法", {"memory_id": raw})
        if raw in seen:
            raise_api_error(422, MEMORY_STATE_CONFLICT, "取代目标重复", {"memory_id": raw})
        seen.add(raw)
        target = db.get(Memory, raw)
        if target is None or target.author_account_id != account_id:
            # 不区分「不存在」与「不属于本人」：两者都是不可取代，
            # 区分它们会让这个接口变成存在性枚举。
            raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
        if target.status != "active" or target.confirmation_status != "confirmed":
            raise_api_error(409, MEMORY_STATE_CONFLICT, "只能取代仍在生效的已确认记忆")
        targets.append(target)
    return targets


def confirm_candidate(
    db: Session,
    *,
    candidate_id: int,
    confirmer: User,
    confirmer_account: Account,
    scope: str,
    space_id: int | None = None,
    content: str | None = None,
    retention_until: datetime | None = None,
    retention_days: int | None = None,
    supersedes: Sequence[int] = (),
) -> Memory:
    """Confirm a candidate with an explicit, user-selected scope.

    The suggested scope is informational only. It can never widen the user's
    explicit selection, and high-sensitivity material cannot be shared.
    """
    _require_memory_enabled(db)
    candidate = db.get(MemoryCandidate, candidate_id, populate_existing=True)
    if (
        candidate is None
        or candidate.author_account_id != confirmer_account.id
        or confirmer_account.user_id != confirmer.id
    ):
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆候选不存在")
    scope, space_id = _parse_memory_scope(scope, space_id)
    _validate_sensitivity(candidate.sensitivity)
    _validate_scope(scope, space_id)
    if retention_days is not None and (
        not 1 <= retention_days <= 3650 or retention_until is not None
    ):
        raise_api_error(422, MEMORY_STATE_CONFLICT, "保留期限参数不合法")
    request = {
        "scope": scope,
        "space_id": space_id,
        "content": content,
        "retention_days": retention_days,
        "retention_until": retention_until.isoformat() if retention_until else None,
        # 取代目标参与 fingerprint：同一个候选配不同取代集是不同请求，
        # 否则重试会拿回一个「部分取代」的结果而不报错。
        "supersedes": sorted({int(value) for value in supersedes}),
    }
    fingerprint = _request_fingerprint(request)
    if candidate.status == "confirmed":
        return _replay_confirmation(db, candidate, confirmer, confirmer_account, fingerprint)
    if candidate.status != "pending":
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆候选已经处理")
    if scope in _SHARED_SCOPES:
        if candidate.sensitivity == "high":
            raise_api_error(422, MEMORY_SENSITIVE_SCOPE_FORBIDDEN, "高敏感记忆不能公开到空间")
        if space_id is None:
            raise_api_error(404, MEMORY_SCOPE_FORBIDDEN, "目标空间不存在")
        space = db.get(FamilySpace, space_id)
        if space is None:
            raise_api_error(404, MEMORY_SCOPE_FORBIDDEN, "目标空间不存在")
        if space.kind != scope:
            raise_api_error(
                422,
                MEMORY_SCOPE_FORBIDDEN,
                "记忆 scope 必须与目标空间类型一致",
                {"scope": scope, "space_kind": space.kind},
            )
        if not _active_space_member(db, user_id=confirmer.id, space_id=space_id):
            raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "只能确认到本人 active 成员所在空间")
    access = memory_sources.source_access(
        db, candidate, actor=confirmer, account=confirmer_account, require_live_message=True
    )
    if not access.readable:
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "原来源待验证、已失效或当前无权读取")
    requested_scope = scope if scope == "private" else f"{scope}:{space_id}"
    if requested_scope not in access.allowed_scopes:
        raise_api_error(422, MEMORY_SCOPE_FORBIDDEN, "确认范围不能超过原来源的授权范围")
    if content is not None and (not content.strip() or len(content) > 20_000):
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆正文为空或超长")
    # 取代目标必须在写锁**之前**解析并校验：失败时不消耗候选状态，也不产生
    # 任何部分写入。
    supersede_targets = _resolve_supersede_targets(
        db, account_id=confirmer_account.id, supersedes=supersedes
    )
    now = utcnow()
    claimed = db.scalar(
        update(MemoryCandidate)
        .where(
            MemoryCandidate.id == candidate.id,
            MemoryCandidate.author_account_id == confirmer_account.id,
            MemoryCandidate.status == "pending",
        )
        .values(
            status="confirmed",
            confirmation_fingerprint=fingerprint,
            confirmed_by_account_id=confirmer_account.id,
            confirmed_at=now,
            decided_at=now,
            updated_at=now,
        )
        .returning(MemoryCandidate.id)
        .execution_options(synchronize_session=False)
    )
    db.refresh(candidate)
    if claimed is None:
        return _replay_confirmation(db, candidate, confirmer, confirmer_account, fingerprint)
    # Acquire the SQLite writer before the final read: no concurrent source
    # revocation can commit between this authorization check and our commit.
    final_access = memory_sources.source_access(
        db, candidate, actor=confirmer, account=confirmer_account, require_live_message=True
    )
    if not final_access.readable:
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "原来源已失效或当前无权读取")
    if requested_scope not in final_access.allowed_scopes:
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "当前已无权确认到目标范围")
    db.get(PlatformFeatureConfig, 1, populate_existing=True)
    _require_memory_enabled(db)
    # 最终读取（writer 已持有）：并发取代/到期必须在这里被观察到，
    # 否则会确认出一个「取代目标是刚刚被取代的旧行」的矛盾状态。
    for target in supersede_targets:
        _require_current_memory(db, target)
    if retention_days is not None:
        retention_until = now + timedelta(days=retention_days)
    memory = Memory(
        author_account_id=candidate.author_account_id,
        source_candidate_id=candidate.id,
        source_message_id=candidate.source_message_id,
        source_document_ref=candidate.source_document_ref,
        source_span_json=candidate.source_span_json,
        source_kind=candidate.source_kind,
        source_verification=candidate.source_verification,
        source_type=candidate.source_type,
        source_id=candidate.source_id,
        source_revision=candidate.source_revision,
        source_space_id=candidate.source_space_id,
        confirmation_request_json=request,
        raw_quote=candidate.source_quote,
        content=content or candidate.summary,
        scope=scope,
        space_id=space_id,
        sensitivity=candidate.sensitivity,
        purpose=candidate.purpose,
        revision=1,
        retention_until=retention_until,
        confirmed_by_account_id=confirmer_account.id,
        confirmed_at=now,
        created_at=now,
        updated_at=now,
        status="active",
    )
    db.add(memory)
    db.flush()
    for target in supersede_targets:
        # 用同一条命令写入取代指针：取代与确认在同一事务内生效，不存在
        # 「新事实已确认、旧事实仍可检索」的中间窗口。
        supersede_memory(
            db,
            memory_id=target.id,
            account_id=confirmer_account.id,
            by_memory_id=memory.id,
        )
    candidate.status = "confirmed"
    candidate.confirmed_by_account_id = confirmer_account.id
    candidate.confirmed_at = now
    candidate.decided_at = now
    candidate.updated_at = now
    candidate.memory_id = memory.id
    if platform_features.is_rag_enabled(db):
        index_memory(db, memory)
    emit_domain_event(
        db,
        event_type="memory.confirmed",
        aggregate_type="memory",
        aggregate_id=memory.id,
        payload={"scope": memory.scope, "space_id": memory.space_id, "revision": memory.revision},
        space_id=memory.space_id,
        actor_account_id=confirmer_account.id,
    )
    return memory


def _replay_confirmation(
    db: Session,
    candidate: MemoryCandidate,
    actor: User,
    account: Account,
    fingerprint: str,
) -> Memory:
    if candidate.status != "confirmed" or candidate.confirmation_fingerprint != fingerprint:
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆候选已经处理或确认参数与首次不同")
    memory = db.scalar(
        select(Memory)
        .where(Memory.source_candidate_id == candidate.id)
        .execution_options(populate_existing=True)
    )
    if (
        memory is None
        or memory.status != "active"
        or (memory.retention_until is not None and memory.retention_until <= utcnow())
    ):
        raise_api_error(409, MEMORY_STATE_CONFLICT, "已确认记忆当前不可用")
    if not memory_sources.memory_access(db, memory, actor=actor, account=account).readable:
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "原来源已失效或当前无权读取")
    return memory


def dismiss_candidate(db: Session, *, candidate_id: int, account_id: int) -> MemoryCandidate:
    _require_memory_enabled(db)
    candidate = db.get(MemoryCandidate, candidate_id)
    if candidate is None or candidate.author_account_id != account_id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆候选不存在")
    if candidate.status == "dismissed":
        return candidate
    if candidate.status != "pending":
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆候选已经处理")
    now = utcnow()
    changed = db.scalar(
        update(MemoryCandidate)
        .where(
            MemoryCandidate.id == candidate_id,
            MemoryCandidate.author_account_id == account_id,
            MemoryCandidate.status == "pending",
        )
        .values(status="dismissed", decided_at=now, updated_at=now)
        .returning(MemoryCandidate.id)
        .execution_options(synchronize_session=False)
    )
    db.refresh(candidate)
    if changed is None:
        if candidate.status == "dismissed":
            return candidate
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆候选已经处理")
    emit_domain_event(
        db,
        event_type="memory.candidate.dismissed",
        aggregate_type="memory_candidate",
        aggregate_id=candidate.id,
        payload={"status": candidate.status},
        actor_account_id=account_id,
    )
    return candidate


def _estimate_tokens(text_value: str) -> int:
    """Declared UTF-8 estimate; actual ContextBuilder includes the full envelope.

    This heuristic is deliberately conservative for ordinary Chinese/English,
    but is not a guarantee about every tokenizer or the full model request.
    """
    return max(1, estimate_tokens(text_value))


def _split_sentences(paragraph: str) -> list[str]:
    """Split one paragraph into sentence-bounded pieces (deterministic)."""
    pieces: list[str] = []
    current: list[str] = []
    for char in paragraph:
        current.append(char)
        if char in "。！？!?；;\n":
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))
    return pieces


def _chunk_text(value: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """Sentence/paragraph-first chunking with bounded, deterministic overlap.

    Very long sentences are hard-split only when a single sentence exceeds
    ``max_chars``; the split point is char-aligned but chunks carry overlap so
    cross-boundary facts remain retrievable from a complete chunk.
    """
    clean = value.strip()
    if not clean:
        return []
    chunks: list[str] = []
    current = ""
    for paragraph in clean.split("\n"):
        for sentence in _split_sentences(paragraph):
            while len(sentence) > max_chars:
                # Hard-split an oversized sentence, keeping the tail as the
                # start of the next piece (bounded overlap by construction).
                if current:
                    chunks.append(current)
                    current = ""
                head = sentence[:max_chars]
                # Prefer not to cut a chunk at zero length; always make progress.
                chunks.append(head)
                sentence = sentence[max_chars - _CHUNK_OVERLAP_CHARS :]
            if not current:
                current = sentence
            elif len(current) + len(sentence) <= max_chars:
                current += sentence
            else:
                chunks.append(current)
                overlap = current[-_CHUNK_OVERLAP_CHARS:] if _CHUNK_OVERLAP_CHARS else ""
                current = overlap + sentence
    if current.strip():
        chunks.append(current)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _chunk_text_v1(value: str) -> list[str]:
    """The historical fixed-width algorithm; never relabel v2 chunks as v1."""
    clean = value.strip()
    return [clean[index : index + 1200] for index in range(0, len(clean), 1200)]


# Only executable algorithms can be targets. Future algorithms must register
# their real implementation; tests may inject synthetic versions here.
INDEX_CHUNKERS: dict[str, Callable[[str], list[str]]] = {
    "fts5-trigram-v1": _chunk_text_v1,
    "fts5-trigram-v2": _chunk_text,
}
MAX_INDEX_INPUT_CHARS = 120_000
MAX_INDEX_CHUNKS = 256


def _index_conflict(message: str = "同一来源版本的索引内容或元数据冲突") -> None:
    raise_api_error(409, RAG_SOURCE_NOT_ALLOWED, message)


def _pieces_for_version(value: str, version: str) -> list[str]:
    chunker = INDEX_CHUNKERS.get(version)
    if chunker is None:
        _index_conflict("索引算法版本未知，保留原投影")
    if len(value) > MAX_INDEX_INPUT_CHARS:
        _index_conflict("来源超过单次索引大小限制")
    assert chunker is not None
    pieces = chunker(value)
    if not pieces or len(pieces) > MAX_INDEX_CHUNKS:
        _index_conflict("来源块数量超出索引范围")
    return pieces


def _acquire_index_writer(db: Session) -> None:
    """Acquire the writer before refreshing mutable sources or flags.

    A no-op UPDATE also starts a real transaction before any SAVEPOINT on
    SQLite's legacy driver. Callers own the short outer commit/rollback.

    ## `WHERE false` 而不是 `WHERE 0`

    SQLite 接受 `WHERE 0`（整数被当布尔），PostgreSQL 不接受：
    `argument of WHERE must be type boolean, not type integer`。实测该语句让
    RAG 索引维护**每 5 秒失败一次**，且失败被吞成 WARNING（`core tick unaffected`），
    所以症状只是「RAG 永远不索引」，而不是任何可见错误。

    这是与迁移 0008 的 `boolean = integer` 同一类 SQLite→PostgreSQL 类型语义差异。
    `WHERE false` 在两种方言下都是布尔假，语义一致。
    """
    db.execute(text("UPDATE rag_documents SET id = id WHERE false"))


def _require_fresh_rag_enabled(db: Session) -> None:
    db.flush()
    db.get(PlatformFeatureConfig, 1, populate_existing=True)
    _require_rag_enabled(db)


def _supersede_guard_sql(alias: str) -> str:
    """取代/有效区间过滤（P1）。`alias` 是 memories 的别名（各查询不同）。

    这是**承重**条件：去掉它，被取代的旧事实会立刻重新进入模型上下文（mutation
    测试会失败）。因为被取代的行**不删除**（历史可审计、取代可撤销），可见性完全
    依赖这条过滤，而不是依赖行是否存在。
    """
    return (
        f"AND {alias}.superseded_by_id IS NULL "
        f"AND ({alias}.valid_to IS NULL OR {alias}.valid_to > :now)"
    )


def _typed_eligibility(sql: Any) -> Any:
    """给方言 SQL 上的 `:now` 声明 DateTime 类型。

    不能把 `bindparam(...)` 放进 params 字典——那会把它当成**值**传给 sqlite3
    （实测 `Error binding parameter: type \'BindParameter\' is not supported`）。
    类型必须挂在语句上。SQLite 上 datetime 列以字符串存储，未声明类型的参数会把
    ISO 串按字符串比较（实测会静默丢行）。
    """
    return sql.bindparams(bindparam("now", type_=DateTime))


def _fresh_materializable_memory(
    db: Session, memory_id: int, *, require_current: bool = True
) -> Memory:
    """Re-read the source under the writer lock and assert it is indexable.

    `require_current=False` is used by the index-version backfill: a memory that
    was superseded mid-batch is still a legal source for its own projection (the
    projection simply becomes unreachable through the eligibility filter). Only
    the source lifecycle and confirmation state must hold.
    """
    db.flush()
    memory = db.get(Memory, memory_id, populate_existing=True)
    if memory is None or not memory_sources.memory_materializable(db, memory):
        _index_conflict("来源未验证或已失效，不能建立索引")
    assert memory is not None
    if require_current and not _memory_is_current(memory):
        _index_conflict("记忆已被取代或已过有效期，不能建立索引")
    return memory


def _memory_is_current(memory: Memory) -> bool:
    """Whether this memory is still eligible for retrieval (same predicate as SQL)."""
    if memory.superseded_by_id is not None:
        return False
    if memory.valid_to is not None and memory.valid_to <= utcnow():
        return False
    return True


def _memory_document_metadata(db: Session, memory: Memory) -> dict[str, Any]:
    return {
        "source_type": "memory",
        "source_id": str(memory.id),
        "author_account_id": memory.author_account_id,
        "owner_user_id": db.scalar(
            select(Account.user_id).where(Account.id == memory.author_account_id)
        ),
        "space_id": memory.space_id,
        "scope": memory.scope,
        "sensitivity": memory.sensitivity,
        "confirmation_status": "confirmed",
        "source_revision": memory.revision,
        "revision": memory.revision,
        "visibility_snapshot": {},
        "visibility_snapshot_key": f"memory:{memory.id}:r{memory.revision}",
    }


def _check_document_metadata(
    document: RAGDocument, expected: dict[str, Any], *, allow_index_superseded: bool = False
) -> None:
    current = document.status == "active" and document.invalidation_reason is None
    superseded = (
        allow_index_superseded
        and document.source_type == "memory"
        and document.status == "invalidated"
        and document.invalidation_reason == "index_superseded"
    )
    if (
        not (current or superseded)
        or document.revision != document.source_revision
        or any(getattr(document, key) != value for key, value in expected.items())
    ):
        _index_conflict()


def _canonical_document(
    db: Session,
    metadata: dict[str, Any],
    *,
    target_version: str,
    allow_index_superseded: bool = False,
) -> tuple[RAGDocument, bool]:
    now = utcnow()
    created_id = db.scalar(
        sqlite_insert(RAGDocument)
        .values(
            **metadata,
            index_version=target_version,
            status="active",
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["source_type", "source_id", "revision"])
        .returning(RAGDocument.id)
    )
    document = db.scalar(
        select(RAGDocument)
        .where(
            RAGDocument.source_type == metadata["source_type"],
            RAGDocument.source_id == metadata["source_id"],
            RAGDocument.revision == metadata["revision"],
        )
        .execution_options(populate_existing=True)
    )
    assert document is not None
    _check_document_metadata(document, metadata, allow_index_superseded=allow_index_superseded)
    return document, created_id is not None


def _version_chunks(
    db: Session, document_id: int, version: str, *, bounded: bool = False
) -> list[RAGChunk]:
    statement = (
        select(RAGChunk)
        .where(RAGChunk.document_id == document_id, RAGChunk.index_version == version)
        .order_by(RAGChunk.chunk_index)
        .execution_options(populate_existing=True)
    )
    if bounded:
        # The extra row proves overflow without loading an unbounded damaged
        # projection during a maintenance batch. Explicit FTS repair can page
        # documents containing legacy material beyond the new input limit.
        statement = statement.limit(MAX_INDEX_CHUNKS + 1)
    return list(db.scalars(statement))


def _chunks_match(rows: Sequence[RAGChunk], pieces: list[str], revision: int) -> bool:
    return all(
        0 <= row.chunk_index < len(pieces)
        and row.text == pieces[row.chunk_index]
        and row.source_revision == revision
        and row.status == "active"
        for row in rows
    )


def _is_sqlite(db: Session) -> bool:
    """本会话是否运行在 SQLite 上。

    ## 为什么必须有这个判据

    `rag_chunks_fts` 是 **SQLite FTS5 虚拟表**，PostgreSQL 上不存在。
    无守卫地写它会让**任何记忆索引都失败**：

    ```text
    psycopg.errors.UndefinedTable: relation "rag_chunks_fts" does not exist
    ```

    实测（生产，2026-10-08）：RAG 从未有过内容，所以这条路径从未被执行，
    缺陷一直隐藏；一旦写入第一条记忆就立刻暴露。PostgreSQL 侧的中文词法检索
    由 PGroonga 承担（见 `rag_search_provider`），不需要 FTS5 投影。
    """
    bind = db.get_bind()
    return bind is None or bind.dialect.name == "sqlite"


def _fts_rows(db: Session, rows: Sequence[RAGChunk]) -> dict[int, tuple[Any, str]]:
    if not rows or not _is_sqlite(db):
        return {}
    records = db.execute(
        text("SELECT rowid, chunk_id, text FROM rag_chunks_fts WHERE rowid IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": [row.id for row in rows]},
    )
    return {record[0]: (record[1], record[2]) for record in records}


def _repair_chunk_fts(db: Session, rows: Sequence[RAGChunk]) -> None:
    # PostgreSQL 上没有 FTS5 投影（见 `_is_sqlite`）；词法检索走 PGroonga。
    if not _is_sqlite(db):
        return
    indexed = _fts_rows(db, rows)
    for row in rows:
        if indexed.get(row.id) == (row.id, row.text):
            continue
        db.execute(text("DELETE FROM rag_chunks_fts WHERE rowid = :id"), {"id": row.id})
        db.execute(
            text("INSERT INTO rag_chunks_fts(rowid, chunk_id, text) VALUES (:id, :id, :value)"),
            {"id": row.id, "value": row.text},
        )


def _materialize_chunks(
    db: Session,
    document: RAGDocument,
    text_value: str,
    revision: int,
    *,
    creating: bool = False,
    target_version: str | None = None,
) -> None:
    """Validate the complete identity before filling gaps; never rewrite a chunk.

    The input digest survives missing chunks, unlike comparing only surviving
    positions. Legacy NULL digests require a complete matching active set;
    neither raw_quote's hash nor a partial set proves the indexing input.
    """
    version = target_version or document.index_version
    pieces = _pieces_for_version(text_value, version)
    digest = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
    if document.revision != revision or document.source_revision != revision:
        _index_conflict()
    if document.content_sha256 is not None and document.content_sha256 != digest:
        _index_conflict()
    existing = _version_chunks(db, document.id, version, bounded=True)
    if not _chunks_match(existing, pieces, revision):
        _index_conflict()
    if document.content_sha256 is None and not creating:
        active = _version_chunks(db, document.id, document.index_version, bounded=True)
        active_pieces = _pieces_for_version(text_value, document.index_version)
        if len(active) != len(active_pieces) or not _chunks_match(active, active_pieces, revision):
            _index_conflict("旧投影缺少完整正文证据，不能补签或补块")
    by_index = {row.chunk_index: row for row in existing}
    for index, value in enumerate(pieces):
        if index not in by_index:
            db.add(
                RAGChunk(
                    document_id=document.id,
                    chunk_index=index,
                    source_revision=revision,
                    text=value,
                    token_estimate=_estimate_tokens(value),
                    index_version=version,
                    status="active",
                    created_at=utcnow(),
                )
            )
    db.flush()
    complete = _version_chunks(db, document.id, version, bounded=True)
    if len(complete) != len(pieces) or not _chunks_match(complete, pieces, revision):
        _index_conflict()
    if document.content_sha256 is None:
        document.content_sha256 = digest
    _repair_chunk_fts(db, complete)


def _memory_projection_complete(db: Session, memory: Memory) -> bool:
    document = db.scalar(
        select(RAGDocument)
        .where(
            RAGDocument.source_type == "memory",
            RAGDocument.source_id == str(memory.id),
            RAGDocument.revision == memory.revision,
        )
        .execution_options(populate_existing=True)
    )
    if document is None or document.status != "active":
        return False
    pieces = _pieces_for_version(memory.content, document.index_version)
    rows = _version_chunks(db, document.id, document.index_version, bounded=True)
    return (
        document.content_sha256 == hashlib.sha256(memory.content.encode("utf-8")).hexdigest()
        and len(rows) == len(pieces)
        and _chunks_match(rows, pieces, memory.revision)
        and _fts_rows(db, rows) == {row.id: (row.id, row.text) for row in rows}
    )


def index_memory(
    db: Session,
    memory: Memory,
    *,
    target_version: str | None = None,
    allow_superseded: bool = False,
) -> RAGDocument:
    """Ensure the canonical projection; existing activity pointers never move here.

    `allow_superseded=True` 只由索引换版回填使用：在批次执行期间被取代的记忆仍应
    完成自己的版本切换（否则游标会停在那里反复重试）。投影虽然完成，但它**不可达**
    ——检索 eligibility 的取代过滤会把它排除。
    """
    _acquire_index_writer(db)
    db.flush()
    with db.begin_nested():
        _require_fresh_rag_enabled(db)
        current = _fresh_materializable_memory(db, memory.id, require_current=not allow_superseded)
        version = target_version or RAG_INDEX_VERSION
        _pieces_for_version(current.content, version)
        metadata = _memory_document_metadata(db, current)
        # Only this Memory entry has just revalidated the global source. The
        # authorized-document entry cannot opt in to historical state recovery.
        document, created = _canonical_document(
            db, metadata, target_version=version, allow_index_superseded=True
        )
        recovering = document.status == "invalidated"
        active_version = document.index_version
        _materialize_chunks(db, document, current.content, current.revision, creating=created)
        current = _fresh_materializable_memory(db, current.id, require_current=not allow_superseded)
        db.refresh(document)
        _check_document_metadata(
            document, _memory_document_metadata(db, current), allow_index_superseded=recovering
        )
        if document.content_sha256 != hashlib.sha256(current.content.encode("utf-8")).hexdigest():
            _index_conflict()
        if recovering:
            # Validate/fill the complete immutable set before activation. A
            # later gate rejection still rolls back this update and FTS/chunks
            # together in the enclosing index savepoint.
            changed = db.execute(
                update(RAGDocument)
                .where(
                    RAGDocument.id == document.id,
                    RAGDocument.source_type == "memory",
                    RAGDocument.status == "invalidated",
                    RAGDocument.invalidation_reason == "index_superseded",
                    RAGDocument.revision == current.revision,
                    RAGDocument.source_revision == current.revision,
                    RAGDocument.index_version == active_version,
                    RAGDocument.content_sha256 == document.content_sha256,
                )
                .values(
                    status="active",
                    invalidation_reason=None,
                    invalidated_at=None,
                    updated_at=utcnow(),
                )
                .execution_options(synchronize_session=False)
            ).rowcount
            if changed != 1:
                _index_conflict()
            db.refresh(document)
        _require_fresh_rag_enabled(db)
    return document


def ingest_authorized_document(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    text_value: str,
    author_account_id: int | None,
    scope: str,
    space_id: int | None,
    sensitivity: str = "normal",
    revision: int = 1,
    visibility_snapshot_key: str = "authorized-v1",
) -> RAGDocument:
    """Ingest only an explicitly authorized non-chat source."""
    if source_type not in RAG_SOURCE_TYPES or source_type == "memory":
        raise_api_error(422, RAG_SOURCE_NOT_ALLOWED, "该来源类型不能通过文档入口索引")
    if not text_value.strip():
        raise_api_error(422, RAG_SOURCE_NOT_ALLOWED, "可索引文档不能为空")
    _validate_rag_scope(scope, space_id)
    _validate_sensitivity(sensitivity)
    if scope == "public" and sensitivity in ("high", "local_required"):
        raise_api_error(422, MEMORY_SENSITIVE_SCOPE_FORBIDDEN, "高敏感文档不能公开到全局")
    if type(revision) is not int or revision < 1:
        raise_api_error(422, RAG_SOURCE_NOT_ALLOWED, "来源版本必须为正整数")
    _acquire_index_writer(db)
    db.flush()
    with db.begin_nested():
        _require_fresh_rag_enabled(db)
        metadata: dict[str, Any] = {
            "source_type": source_type,
            "source_id": source_id,
            "author_account_id": author_account_id,
            "owner_user_id": db.scalar(
                select(Account.user_id).where(Account.id == author_account_id)
            )
            if author_account_id is not None
            else None,
            "space_id": space_id,
            "scope": scope,
            "sensitivity": sensitivity,
            "confirmation_status": "authorized",
            "source_revision": revision,
            "revision": revision,
            "visibility_snapshot": {},
            "visibility_snapshot_key": visibility_snapshot_key,
        }
        document, created = _canonical_document(db, metadata, target_version=RAG_INDEX_VERSION)
        _materialize_chunks(db, document, text_value, revision, creating=created)
        _require_fresh_rag_enabled(db)
        if created:
            emit_domain_event(
                db,
                event_type="rag.document.ingested",
                aggregate_type="rag_document",
                aggregate_id=document.id,
                payload={"source_type": source_type, "scope": scope, "revision": revision},
                space_id=space_id,
                actor_account_id=author_account_id,
            )
        db.flush()
    return document


def _fts_match(value: str) -> str:
    # Match as one quoted phrase. This prevents FTS operators from changing the
    # query while retaining trigram matching for CJK and short text.
    return '"' + value.replace('"', '""') + '"'


# `private_reader_account_id` 未传时的哨兵与 `memory_sources` 共用（同一类型，
# 否则类型检查器会认为是两个不同的类型）。语义见 memory_sources._UnsetReader。
_UNSET_READER = memory_sources._UNSET_READER
_UnsetReader = memory_sources._UnsetReader


def _validated_source_types(source_types: Sequence[str]) -> tuple[str, ...]:
    """只允许 `RAG_SOURCE_TYPES` 内的值。

    这些值会被拼进 SQL 片段，因此必须是**枚举白名单**而不是任意字符串；
    未知值直接 422，不静默丢弃。
    """
    unknown = sorted({value for value in source_types if value not in RAG_SOURCE_TYPES})
    if unknown:
        raise_api_error(
            422, POLICY_CONTEXT_INVALID, "未知的 RAG source_type", {"source_types": unknown}
        )
    return tuple(value for value in RAG_SOURCE_TYPES if value in set(source_types))


# SQL eligibility predicates shared by the FTS path and the short-word fallback
# so a two-character query can never reach raw rows the FTS path cannot.
#
# 每个 scope 分支都有自己的允许开关（`:allow_private` / `:allow_household` /
# `:allow_lineage`），因为「调用方可以读哪些级别」是**集合**而非布尔：管家按配置
# 可能只允许 `household` 而不允许 `lineage`。开关与身份判据是两道独立的门，
# 两者都必须过：开关来自调用方声明的允许集，身份来自 fenced 身份。
#
# private 分支**不再用** `:is_assistant = 1`，而是显式要求
# `d.author_account_id = :private_reader_account_id`。旧写法把「能否读私有」与
# 「调用方是什么 kind」绑定，而管家的空间级 kind 会回落到 space admin 作为身份，
# 一旦为管家打开 private 就会读到管理员本人的私事。改成显式读者后，私有记忆
# 的语义是「只能被它的作者账号读」，而管家只在带 viewer 时才能提供这个账号，
# 空间级 kind 传 NULL 而 `author_account_id = NULL` 恒假。
# `:is_assistant` 仍保留在 public 分支：无限制公开材料不对管家开放。
_ELIGIBILITY_SQL = """
  c.status = 'active'
  AND d.status = 'active'
  AND c.index_version = d.index_version
  AND c.source_revision = d.revision AND d.source_revision = d.revision
  AND d.invalidation_reason IS NULL
  AND d.confirmation_status IN ('confirmed', 'authorized')
  AND (d.source_type != 'memory' OR EXISTS (
    SELECT 1 FROM memories m WHERE CAST(m.id AS TEXT) = d.source_id
      AND m.status = 'active' AND m.confirmation_status = 'confirmed'
      AND m.source_verification = 'verified' AND m.revision = d.revision
      AND m.superseded_by_id IS NULL
      AND (m.valid_to IS NULL OR m.valid_to > :now)
  ))
  {sensitivity}
  {source_types}
  AND (
    (d.scope = 'private' AND :allow_private = 1
     AND d.author_account_id = :private_reader_account_id)
    OR
    (d.scope IN ('household', 'lineage') AND d.space_id = :space_id
     AND ((d.scope = 'household' AND :allow_household = 1)
          OR (d.scope = 'lineage' AND :allow_lineage = 1))
     AND EXISTS (
       SELECT 1 FROM space_members sm
       WHERE sm.space_id = d.space_id AND sm.user_id = :user_id AND sm.status = 'active'
     ))
    OR
    (d.scope = 'public' AND :is_assistant = 1)
  )
"""

_HIT_SQL = """
    SELECT c.id AS chunk_id, d.id AS document_id, d.source_type, d.source_id, c.text,
           c.token_estimate, d.scope, d.sensitivity, d.revision, c.index_version,
           c.chunk_index, c.source_revision
    FROM rag_chunks AS c
    JOIN rag_documents AS d ON d.id = c.document_id
    WHERE {condition}
      AND {eligibility}
    ORDER BY {ordering}
    LIMIT :limit OFFSET :offset
"""

# Candidate budget shared by FTS and the parameterized short-word fallback.
# SQLite's internal work is separate from the number of returned candidates.
_FALLBACK_SCAN_LIMIT = 200
_SCAN_PAGE_SIZE = 32
#: 候选池上限。必须 >= 调用方 limit，否则「收集候选再重排」会退化成截断。
#: 定在 100：与 `_HIT_SQL` 的 `limit <= 100` 约束同量级，且一次查询的候选数
#: 再多也不会改变 top-k（重排是确定性的全序）。
_CANDIDATE_BUDGET = 100

#: 重排版本。`lex-v1` = 旧顺序（候选到达顺序，显式回退开关）；
#: `lex-v2` = 分支共识 + 来源类别 + 词法分数的确定性重排。
RANK_VERSION_LEGACY = "lex-v1"
RANK_VERSION_DEFAULT = "lex-v2"
RANK_VERSIONS = (RANK_VERSION_LEGACY, RANK_VERSION_DEFAULT)

#: 向量候选的**相似度地板**（余弦相似度，非距离）。低于它的向量候选一律丢弃。
#:
#: ## 取值是实测出来的，不是拍的
#:
#: 在真实 PostgreSQL + 真实 `bge-small-zh-v1.5` 上测得 golden set 的 top-1 相似度：
#:
#: ```text
#: 相关命中     min = 0.5091   p50 = 0.6580   max = 0.7716
#: 不相关命中   min = 0.3634   p50 = 0.5000   max = 0.7712   ← 重叠！
#: 三个弃答用例的虚假 top-1：0.4859 / 0.3811 / 0.3634
#: ```
#:
#: 两个分布**重叠**（不相关的最高 0.7712 高于相关的最低 0.5091），因此余弦相似度
#: **不能**作为相关性判据——这是「不把向量当重排依据」的实测根据。
#:
#: 但存在一个可用区间：弃答用例的虚假命中最高 **0.4859**，相关命中最低 **0.5091**。
#: 地板取 0.50 落在这个 0.023 宽的间隙里。它只做一件事：**丢掉明显无关的向量补充**，
#: 从而让「库里没有」仍然表现为空结果（否则向量候选会填满 limit，弃答正确率从
#: 1.00 掉到 0.00——实测过）。
#:
#: ## 为什么地板窄是可以接受的
#:
#: 地板**只作用于向量新增的候选**，不作用于词法命中。因此它误伤一个「弱相关」
#: 向量候选的代价是零——词法路径本来就已经提供了那条命中。它换来的是弃答语义。
#:
#: ## 换 embedding 模型必须重新测量
#:
#: 0.50 是**这个模型在这个语料上**的值。换模型（或换 chunking 算法）后相似度
#: 尺度会变，这个地板必须重新用 `scripts/migration-proof/vector_similarity_distribution.py`
#: 测量后再定，不能沿用。
_VECTOR_MIN_SIMILARITY = 0.50

#: `lex-v2` 的来源类别权重。用户确认的记忆排在最前：它是「用户说过且明确确认
#: 要记住」的事实，比从文档里检索到的段落更可能是用户想听的答案。
_SOURCE_TYPE_RANK_WEIGHT: dict[str, int] = {
    "memory": 3,
    "family_story": 2,
    "profile": 1,
    "authorized_document": 1,
    "public_kinship": 0,
}
_SOURCE_TYPE_RANK_DEFAULT_WEIGHT = 0


def _term_overlap_score(text: str, terms: Sequence[str]) -> int:
    """查询词与正文的重叠度：命中词的**长度之和**。

    ## 为什么这个信号足够

    它替代的是一个**更差**的现状——LIKE 后备分支的 `rank` 只是行号（`c.id ASC`），
    与相关度完全无关，因此谁被返回过去完全取决于插入顺序。实测
    `multi-session-story`（期望 `story-老宅` + `story-桂花`）失败正是因为
    `story-桂花` 的 id 排在 5 条只命中「苏州」的噪声之后。

    这是**词法**信号，不是语义理解：它不知道 `桂花` 与 `糖藕` 的关系，只知道查询里
    出现的词在正文里出现了多少。按长度加权给更具体的词更高权重（`苏州老宅` 4 分 >
    `里的` 2 分）。
    """
    score = 0
    for term in terms:
        if term and term in text:
            score += len(term)
    return score


def rank_candidates(
    candidates: Sequence[RAGHit],
    *,
    branch_hits: dict[int, set[str]],
    rank_version: str,
    limit: int,
    query_terms: Sequence[str] = (),
) -> list[RAGHit]:
    """Deterministic rerank of the candidate pool.

    ## 为什么是确定性特征而不是模型重排

    评分必须**可复现**：同一份数据、同一版本必须给出同一顺序，否则
    `ContextBuild` 的「每次执行不可变」与 `_replay` 的一致性都无从验证。
    模型重排（cross-encoder）的质量增益需要先有基线余量证明，且要经 provider
    gateway（见 `10-09` 的 P3 决策）。

    ## 特征（全部来自已有数据，零额外查询）

    1. **分支共识**：被两个及以上检索分支命中的 chunk 更可能是真正相关的
       （`lex-v2` 的主要增益来源——两字词 LIKE 分支过去被主分支饿死）。
    2. **来源类别**：用户确认的记忆 > 家族故事 > 授权文档 > 公共亲缘。
    3. **词法分**：`rank`（bm25 / pgroonga 分，越小越相关或越大越相关由分支决定，
       因此这里只用它做**同权重内**的稳定次级排序，不做跨分支比较）。
    4. **chunk 位置**：同一文档靠前的片段优先（文档通常先讲重点）。

    `lex-v1` 保留候选到达顺序，作为显式回退。
    """
    if rank_version not in RANK_VERSIONS:
        raise_api_error(
            422, POLICY_CONTEXT_INVALID, "未知的 rank_version", {"rank_version": rank_version}
        )
    if rank_version == RANK_VERSION_LEGACY:
        return list(candidates[:limit])

    def key(hit: RAGHit) -> tuple[int, int, int, int, int]:
        branches = branch_hits.get(hit.chunk_id, set())
        return (
            # 负号使排序为「大在前」。
            -_term_overlap_score(hit.text, query_terms),
            -len(branches),
            -_SOURCE_TYPE_RANK_WEIGHT.get(hit.source_type, _SOURCE_TYPE_RANK_DEFAULT_WEIGHT),
            hit.chunk_index if hit.chunk_index is not None else 0,
            # 最终稳定器：chunk_id 唯一，保证全序（同分时顺序不含随机性）。
            hit.chunk_id,
        )

    return sorted(candidates, key=key)[:limit]


def _rows_to_hits(
    db: Session,
    rows: Any,
    *,
    actor: User,
    account: Account,
    space_id: int,
    agent_kind: str,
    rank_by_order: bool,
    private_reader_account_id: int | None | _UnsetReader = _UNSET_READER,
) -> tuple[list[RAGHit], int]:
    """Project rows through the visibility policy once more; count denials.

    这里除了 `document_readable` 之外还要重查记忆的取代/有效期状态：搜索候选可能
    在 SQL 执行后、本行读取前被取代（并发确认）。只在 SQL 层过滤会留下一个
    窗口，让刚被取代的旧事实进入模型上下文。
    """
    hits: list[RAGHit] = []
    denied = 0
    supersede_cache: dict[int, bool] = {}
    for position, row in enumerate(rows):
        document_id = int(row["document_id"])
        document = db.get(RAGDocument, document_id)
        if document is not None and str(row["source_type"]) == "memory":
            source_key = str(row["source_id"])
            if source_key.isdigit():
                memory_id = int(source_key)
                if memory_id not in supersede_cache:
                    memory = db.get(Memory, memory_id, populate_existing=True)
                    supersede_cache[memory_id] = memory is not None and _memory_is_current(memory)
                if not supersede_cache[memory_id]:
                    denied += 1
                    continue
        if document is None or not memory_sources.document_readable(
            db,
            document,
            actor=actor,
            account=account,
            space_id=space_id,
            agent_kind=agent_kind,
            # 同一层的独立把关：SQL 已经按允许集/读者过滤过，这里再按读者过滤一次。
            private_reader_account_id=private_reader_account_id,
        ):
            denied += 1
            continue
        hits.append(
            RAGHit(
                document_id=document_id,
                chunk_id=int(row["chunk_id"]),
                source_id=str(row["source_id"]),
                text=str(row["text"]),
                token_estimate=int(row["token_estimate"]),
                # Lexical branches are fused by explicit order rank, never by
                # comparing incompatible raw bm25 scores across branches.
                rank=float(position if rank_by_order else row["rank"]),
                citation_handle=f"rag:{row['source_id']}:r{row['revision']}:c{row['chunk_id']}",
                scope=str(row["scope"]),
                sensitivity=str(row["sensitivity"]),
                revision=int(row["revision"]),
                source_type=str(row["source_type"]),
                index_version=str(row["index_version"]),
                space_id=document.space_id,
                allowed_scopes=memory_sources.document_allowed_scopes(db, document, actor),
                chunk_index=int(row["chunk_index"]),
                source_revision=int(row["source_revision"]),
                content_hash=memory_sources.quote_hash(str(row["text"])),
            )
        )
    return hits, denied


def search_rag(
    db: Session,
    *,
    actor: User,
    account: Account,
    space_id: int,
    query: str,
    agent_kind: str = "assistant",
    limit: int = 20,
    provider_kind: str | None = None,
    raise_on_restricted: bool = False,
    for_model: bool = True,
    recent_messages: Sequence[str] = (),
    query_plan: QueryPlan | None = None,
    trace: dict[str, Any] | None = None,
    rank_version: str = RANK_VERSION_DEFAULT,
    scope_allowlist: Sequence[str] | None = None,
    private_reader_account_id: int | None | _UnsetReader = _UNSET_READER,
    source_types: Sequence[str] | None = None,
) -> list[RAGHit]:
    """Search with SQL scope/confirmation/status predicates before results escape.

    Retrieval is planned (``rag_query.plan_query``): the FTS branch ORs the
    exact phrase and bounded terms; two-character Chinese terms that cannot
    trigram-match take a parameterized LIKE fallback restricted to the same
    eligibility predicates with a bounded scan budget.

    ``scope_allowlist`` 声明调用方可以读哪些 scope（``None`` = 全部，即既有行为）；
    ``private_reader_account_id`` 声明 private 分支的读者账号（未传 = 调用方自己的
    ``account.id``，即 assistant 语义；显式传 ``None`` = 无私有读者，恒不可读）。
    两者都是**额外**的门，不替代 fenced 身份与 space 成员判据。

    ``source_types`` 限定来源类别（``None`` = 全部）。只接受 ``RAG_SOURCE_TYPES``
    内的值（枚举白名单，不是拼 SQL 的借口）：它服务**目的限定**，不是授权——
    授权仍由 eligibility 承担。
    """
    _require_rag_enabled(db)
    if rank_version not in RANK_VERSIONS:
        raise_api_error(
            422, POLICY_CONTEXT_INVALID, "未知的 rank_version", {"rank_version": rank_version}
        )
    if not is_policy_consumer_kind(agent_kind):
        raise_api_error(422, MEMORY_SCOPE_FORBIDDEN, "policy consumer 不受支持")
    # 按调用方声明的允许集生成 per-scope 开关。None = 既有行为（三个 scope 全开）；
    # assistant 与管家都显式传值，因此“默认全开”只会出现在未改造的调用点上。
    allow = set(MEMORY_SCOPES) if scope_allowlist is None else set(scope_allowlist)
    unknown_allow = allow - set(MEMORY_SCOPES)
    if unknown_allow:
        raise_api_error(
            422, MEMORY_SCOPE_FORBIDDEN, "未知的 memory scope", {"scopes": sorted(unknown_allow)}
        )
    # private 的读者：未传 = 调用方自己的账号（assistant 语义）；显式 None = 无读者。
    reader_account_id: int | None = (
        account.id
        if isinstance(private_reader_account_id, _UnsetReader)
        else private_reader_account_id
    )
    # public 仍由 kind 决定：无限制公开材料不对管家开放。
    is_assistant = int(agent_kind == "assistant")
    plan = query_plan or plan_query(query, recent_messages=recent_messages)
    if trace is not None:
        trace.update(
            {
                **plan.log_summary(),
                "scanned": 0,
                "denied": 0,
                "returned": 0,
                "scan_limit": _FALLBACK_SCAN_LIMIT,
                "stop_reason": "empty_query",
            }
        )
    if not plan.normalized_query:
        return []
    if not _active_space_member(db, user_id=actor.id, space_id=space_id):
        return []
    expire_due_memories(db, account_id=account.id, space_id=space_id)
    limit = max(1, min(limit, 100))
    # Restricted material is eligible only when the selected provider is local.
    sensitivity_predicate = (
        "AND d.sensitivity IN ('normal','sensitive')"
        if for_model and provider_kind != "local" and not raise_on_restricted
        else ""
    )
    params: dict[str, Any] = {
        "user_id": actor.id,
        "space_id": space_id,
        "is_assistant": is_assistant,
        # 每个 scope 分支的允许开关 + private 的读者账号。由调用方声明的
        # `scope_allowlist` / `private_reader_account_id` 派生，是 fenced 身份
        # 之外的额外一道门。
        "allow_private": int("private" in allow),
        "allow_household": int("household" in allow),
        "allow_lineage": int("lineage" in allow),
        "private_reader_account_id": reader_account_id,
        # 取代/有效区间过滤的比较基准。必须显式绑定 DateTime 类型：
        # SQLite 上 datetime 列以字符串存储，未绑定类型的参数会把 ISO 串当字符串
        # 比较（实测会静默丢行）。
        "now": utcnow(),
    }
    eligibility = _ELIGIBILITY_SQL.format(
        sensitivity=sensitivity_predicate,
        source_types=(
            ""
            if source_types is None
            else "AND d.source_type IN ("
            + ",".join(f"'{value}'" for value in _validated_source_types(source_types))
            + ")"
        ),
    )

    # ---- 候选收集（不再在 limit 处短路） ----
    #
    # 旧实现让 `collect` 在 `len(hits) >= limit` 时立即停止，因此**先运行的分支会
    # 饿死后面的分支**：主 FTS 分支一旦填满 limit，两字词的 LIKE 后备分支根本不会
    # 执行。实测表现就是「一个问题同时指向两条记忆时只召回其中一条」（quality 层
    # `multi-session-story` 失败）。
    #
    # 现在改为：所有分支都收集候选（受 `_CANDIDATE_BUDGET` 约束）→ 确定性重排 →
    # 取 top-k。`candidates` 与 `hits` 的区别是：候选是「找到的」，命中是「返回的」。
    candidates: list[RAGHit] = []
    seen_chunk_ids: set[int] = set()
    branch_hits: dict[int, set[str]] = {}
    scanned = 0
    denied = 0

    def collect(
        sql: Any, branch_params: dict[str, Any], *, rank_by_order: bool, branch: str
    ) -> None:
        nonlocal scanned, denied
        offset = 0
        while len(candidates) < _CANDIDATE_BUDGET and scanned < _FALLBACK_SCAN_LIMIT:
            page_size = min(_SCAN_PAGE_SIZE, _FALLBACK_SCAN_LIMIT - scanned)
            rows = (
                db.execute(
                    _typed_eligibility(sql),
                    {**branch_params, "limit": page_size, "offset": offset},
                )
                .mappings()
                .all()
            )
            scanned += len(rows)
            offset += len(rows)
            page, rejected = _rows_to_hits(
                db,
                rows,
                actor=actor,
                account=account,
                space_id=space_id,
                agent_kind=agent_kind,
                rank_by_order=rank_by_order,
                private_reader_account_id=reader_account_id,
            )
            denied += rejected
            for hit in page:
                if (
                    for_model
                    and provider_kind != "local"
                    and hit.sensitivity in ("high", "local_required")
                ):
                    if raise_on_restricted:
                        raise_api_error(
                            409,
                            PROVIDER_LOCAL_REQUIRED_UNAVAILABLE,
                            "敏感 Context 需要可用的本地 Provider",
                        )
                    denied += 1
                    continue
                # 同一 chunk 可能被多个分支命中：记录它命中了哪些分支（重排特征），
                # 但只保留一份候选。
                branch_hits.setdefault(hit.chunk_id, set()).add(branch)
                if hit.chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(hit.chunk_id)
                candidates.append(hit)
                if len(candidates) >= _CANDIDATE_BUDGET:
                    break
            if len(rows) < page_size:
                break

    # Each branch has stable SQL ordering; both share one candidate budget.
    # Policy rejection refills from the next page instead of exhausting the
    # caller's result limit. The bound counts returned SQL candidates, not the
    # database engine's internal index/table operations.
    # 词法检索按**方言**分派（`rag_search_provider`）：
    #   SQLite      -> FTS5 `MATCH` + `bm25`，短词用参数化 LIKE 后备
    #   PostgreSQL  -> PGroonga `&@~` + `pgroonga_score`（不需要短词后备）
    #
    # `eligibility` 原样传入并拼进两种方言的 SQL：授权过滤**不在** provider 层，
    # 因为检索索引不承载授权（撤权只改主表状态，索引条目仍在）。任何在这里
    # 放宽过滤的改动都是授权漏洞。
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    match_terms = ([plan.phrase] if plan.phrase else []) + list(plan.fts_terms)
    # 循环变量不叫 `query`：那会遮蔽本函数的 `query: str` 参数（mypy 报类型冲突）。
    # `collect` 的每个分支都要带上 `now` 的类型声明。
    for lexical in rag_search_provider.build_lexical(
        dialect,
        match_terms=match_terms,
        fallback_terms=list(plan.fallback_terms),
        eligibility=eligibility,
        hit_sql=_HIT_SQL,
    ):
        if len(candidates) >= _CANDIDATE_BUDGET or scanned >= _FALLBACK_SCAN_LIMIT:
            break
        collect(
            lexical.sql,
            {**params, **lexical.params},
            rank_by_order=lexical.rank_by_order,
            branch=f"lexical:{len(branch_hits)}",
        )

    # ---- 向量候选：**可选增强**，失败不改变检索结果 ----
    #
    # 三条硬性约束（每条都有测试）：
    #
    # 1. **只增不减**：向量候选只能**补充**词法结果，不能替换或减少它们。
    #    若向量路径返回空（embedding 不可用、无向量、低相关性），必须保持词法结果。
    # 2. **不重排已有命中**：词法命中保持原有顺序。RRF 只用于把**新增**候选
    #    插到合适位置。原因：词法顺序是既有行为，改变它会让所有历史回归失效，
    #    而向量质量尚未经中文基准验证。
    # 3. **复用同一授权过滤**：向量查询用 `_ELIGIBILITY_SQL`（与词法完全相同）。
    #    检索索引**不承载授权**（撤权只改主表状态，索引条目仍在），因此这里
    #    少一个条件就是授权漏洞。
    vector_hits, vector_denied = _vector_candidates(
        db,
        actor=actor,
        account=account,
        space_id=space_id,
        agent_kind=agent_kind,
        query=query,
        eligibility=eligibility,
        seen_chunk_ids=seen_chunk_ids,
        limit=limit,
        # eligibility 里的 `:now` / `:is_assistant` / `:user_id` 必须一并传入：
        # 向量 SQL 与词法 SQL 共用同一段 eligibility，漏传任何一个绑定参数都会让
        # 整条查询抛 StatementError 并**静默回退词法**（实测 P1 引入 `:now` 后
        # 向量路径就一直是死的，而日志只说「回退词法结果」）。
        now=params["now"],
        is_assistant=params["is_assistant"],
        user_id=params["user_id"],
        allow_private=params["allow_private"],
        allow_household=params["allow_household"],
        allow_lineage=params["allow_lineage"],
        private_reader_account_id=params["private_reader_account_id"],
    )
    denied += vector_denied
    if trace is not None:
        trace["vector_candidates"] = len(vector_hits)
    for hit in vector_hits:
        if len(candidates) >= _CANDIDATE_BUDGET:
            break
        if hit.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(hit.chunk_id)
        branch_hits.setdefault(hit.chunk_id, set()).add("vector")
        candidates.append(hit)

    # ---- 确定性重排 ----
    #
    # `rank_version` 决定顺序，并进入 `policy_json`（由 ContextBuilder 记录），
    # 使「同一 build 的重放必须给出同一顺序」可被审计。
    hits = rank_candidates(
        candidates,
        branch_hits=branch_hits,
        rank_version=rank_version,
        limit=limit,
        # 重排用的查询词 = 计划里的全部词（含 phrase）。这是唯一新增的输入，
        # 且来自调用方已经算好的计划，不引入新的查询或模型调用。
        query_terms=[
            *([plan.phrase] if plan.phrase else []),
            *plan.fts_terms,
            *plan.fallback_terms,
        ],
    )
    if trace is not None:
        trace.update(
            {
                "scanned": scanned,
                "denied": denied,
                "returned": len(hits),
                "candidates": len(candidates),
                "multi_branch_candidates": sum(
                    1 for hit in candidates if len(branch_hits.get(hit.chunk_id, ())) > 1
                ),
                "rank_version": rank_version,
                "stop_reason": "candidate_budget"
                if len(candidates) >= _CANDIDATE_BUDGET
                else "scan_limit"
                if scanned >= _FALLBACK_SCAN_LIMIT
                else "exhausted",
            }
        )
    return hits


def _vector_candidates(
    db: Session,
    *,
    actor: User,
    account: Account,
    space_id: int,
    agent_kind: str,
    query: str,
    eligibility: str,
    seen_chunk_ids: set[int],
    limit: int,
    now: Any,
    is_assistant: int,
    user_id: int,
    allow_private: int,
    allow_household: int,
    allow_lineage: int,
    private_reader_account_id: int | None,
) -> tuple[list[RAGHit], int]:
    """取向量候选（filter-then-ANN），返回 `(命中, 被拒数)`。任何失败返回空。

    ## 为什么必须复用 `_rows_to_hits`

    这是本函数最重要的安全性质。`_rows_to_hits` 会：

    1. 对每个候选**再次**执行 `memory_sources.document_readable(...)` 可见性判定；
    2. 生成 `citation_handle`、`allowed_scopes`、`content_hash`。

    若向量路径自己构造 `RAGHit`，就会**绕过第 1 条**——而检索索引**不承载授权**
    （实测：撤权只改主表状态，索引条目仍在）。那意味着向量路径能返回用户已无权
    看到的文档。因此这里把行交给同一个构造函数，使两条路径的授权与引用投影
    完全一致。

    ## 为什么失败必须返回空而不是抛错

    Embedding 是**可选加速**。它不可用时检索必须继续用词法路径——否则「向量服务
    挂了」会升级成「用户问不了问题」。

    ## 为什么用 filter-then-ANN

    实测 post-filter 在低选择性下静默返回不足 k（允许 1/10 空间时只剩 1 条）。
    而 RAG **无法区分**「无相关内容」与「被授权过滤掉」，因此必须先在授权集合内
    过滤，再排序。见 `rag_embeddings.build_vector_candidates`。
    """
    from app.services import embedding_client, rag_embeddings

    if not embedding_client.enabled():
        return [], 0
    if db.bind is None or db.bind.dialect.name != "postgresql":
        # 向量存储需要 pgvector；SQLite 上没有该类型，直接跳过（不是错误）。
        return [], 0

    dimension = rag_embeddings.configured_dimension()
    if dimension <= 0:
        return [], 0

    try:
        result = _run_coro_blocking(embedding_client.embed_query(query))
    except Exception:  # noqa: BLE001 - 任何失败都回退词法
        return [], 0
    if not result.usable or not result.vectors:
        return [], 0
    if result.dimension != dimension:
        # 维度不一致说明配置漂移（模型换了但 RAG_EMBEDDING_DIMENSION 未同步）。
        # 不能猜着用：`vector(N)` 不匹配会让查询直接报错。
        logger.warning(
            "embedding 维度 %d 与配置 %d 不一致，跳过向量检索",
            result.dimension,
            dimension,
        )
        return [], 0

    literal = "[" + ",".join(f"{v:.7f}" for v in result.vectors[0]) + "]"
    sql = rag_embeddings.build_vector_candidates(
        dimension=dimension,
        eligibility=eligibility,
        model=rag_embeddings.configured_model(),
    )
    try:
        rows = (
            db.execute(
                _typed_eligibility(sql),
                {
                    "query_vector": literal,
                    "model": rag_embeddings.configured_model(),
                    "limit": limit * 2,
                    "offset": 0,
                    # eligibility 的绑定参数（与词法路径同源）。
                    "now": now,
                    "is_assistant": is_assistant,
                    "user_id": user_id,
                    "space_id": space_id,
                    "allow_private": allow_private,
                    "allow_household": allow_household,
                    "allow_lineage": allow_lineage,
                    "private_reader_account_id": private_reader_account_id,
                },
            )
            .mappings()
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - 表不存在/扩展缺失等都回退
        db.rollback()
        # 必须记录 error_class：否则「向量路径静默回退」这件事无法诊断——
        # 实测在 PostgreSQL 上 `:is_assistant = 1` 触发 `boolean = integer`
        # 类型错误，而原日志只有一句「回退词法结果」，从现象看不出原因。
        logger.warning(
            "向量检索查询失败，回退词法结果 error_class=%s detail=%s",
            type(exc).__name__,
            str(exc)[:200],
        )
        return [], 0

    # 交给同一个构造函数：授权复核与引用投影与词法路径完全一致。
    hits, denied = _rows_to_hits(
        db,
        rows,
        actor=actor,
        account=account,
        space_id=space_id,
        agent_kind=agent_kind,
        rank_by_order=False,
        private_reader_account_id=private_reader_account_id,
    )
    # 相似度地板：`rank` 是**余弦距离**（`<=>`，0 = 完全相同），因此相似度 = 1 - rank。
    # 没有这个地板，向量候选会填满 limit，把「库里没有」变成「随便返回几条」。
    kept = [
        hit
        for hit in hits
        if hit.chunk_id not in seen_chunk_ids and (1.0 - float(hit.rank)) >= _VECTOR_MIN_SIMILARITY
    ]
    return kept, denied


def _run_coro_blocking(coro: Any) -> Any:
    """在同步上下文中跑一个协程。

    若当前线程已有运行中的事件循环（例如从 async 端点调用），
    `asyncio.run` 会抛 `RuntimeError`。此时在**新线程**里跑，避免与现有循环冲突。
    新线程是必要的：不能阻塞调用方的事件循环。
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的循环：直接跑（常见路径：请求线程/工具线程）。
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    from app.services import embedding_client

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=embedding_client.REQUEST_TIMEOUT_SECONDS + 5)
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        raise TimeoutError("embedding 客户端线程未在超时内结束")
    return box["value"]


def expire_due_memories(
    db: Session,
    *,
    account_id: int | None = None,
    space_id: int | None = None,
    now: datetime | None = None,
) -> int:
    """Tombstone expired memories and their RAG rows in the caller's transaction."""
    moment = now or utcnow()
    stmt = select(Memory).where(
        Memory.status == "active",
        Memory.retention_until.is_not(None),
        Memory.retention_until <= moment,
    )
    if space_id is not None:
        if account_id is None:
            stmt = stmt.where(Memory.space_id == space_id)
        else:
            stmt = stmt.where(
                or_(
                    Memory.space_id == space_id,
                    and_(Memory.scope == "private", Memory.author_account_id == account_id),
                )
            )
    elif account_id is not None:
        stmt = stmt.where(
            Memory.scope == "private",
            Memory.author_account_id == account_id,
        )
    else:
        return 0
    rows = db.scalars(stmt).all()
    for memory in rows:
        memory.status = "deleted"
        memory.deleted_at = moment
        memory.updated_at = moment
        invalidate_source(db, source_type="memory", source_id=str(memory.id))
        emit_domain_event(
            db,
            event_type="memory.expired",
            aggregate_type="memory",
            aggregate_id=memory.id,
            payload={"status": memory.status, "revision": memory.revision},
            space_id=memory.space_id,
            actor_account_id=account_id,
        )
    if rows:
        db.flush()
    return len(rows)


def invalidate_for_domain_event(
    db: Session,
    *,
    event_type: str,
    aggregate_id: int,
    payload: dict[str, object],
) -> int:
    """Apply immediate RAG tombstones for destructive domain events.

    Membership and disclosure changes remain dynamically authorized by ``search_rag``;
    only source destruction needs a durable tombstone because the source row can
    disappear or lose its author foreign key during the same transaction.
    """
    owner_user_id: int | None = None
    if event_type == "profile.deleted":
        owner_user_id = aggregate_id
    elif event_type == "data_right.delete.executed":
        raw_profile_id = payload.get("profile_id")
        if isinstance(raw_profile_id, int):
            owner_user_id = raw_profile_id
    if owner_user_id is None:
        return 0
    rows = db.scalars(
        select(RAGDocument).where(
            RAGDocument.owner_user_id == owner_user_id,
            RAGDocument.status == "active",
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.status = "invalidated"
        row.invalidated_at = now
        row.updated_at = now
        db.query(RAGChunk).filter(
            RAGChunk.document_id == row.id, RAGChunk.status == "active"
        ).update(
            {RAGChunk.status: "invalidated", RAGChunk.updated_at: now},
            synchronize_session=False,
        )
    return len(rows)


def invalidate_source(
    db: Session, *, source_type: str, source_id: str, revision: int | None = None
) -> int:
    """Tombstone first: authorization stops matching before physical cleanup."""
    stmt = select(RAGDocument).where(
        RAGDocument.source_type == source_type,
        RAGDocument.source_id == source_id,
        RAGDocument.status == "active",
    )
    if revision is not None:
        stmt = stmt.where(RAGDocument.revision <= revision)
    rows = db.scalars(stmt).all()
    now = utcnow()
    for row in rows:
        row.status = "invalidated"
        row.invalidation_reason = "source_invalidated"
        row.invalidated_at = now
        row.updated_at = now
        db.query(RAGChunk).filter(
            RAGChunk.document_id == row.id, RAGChunk.status == "active"
        ).update(
            {RAGChunk.status: "invalidated", RAGChunk.updated_at: now},
            synchronize_session=False,
        )
    return len(rows)


def _require_current_memory(db: Session, memory: Memory) -> Memory:
    """Writer-level recheck: a concurrent supersede/expire must be observed.

    在取得写锁后、最终授权校验之前调用（与 `memory_sources.source_access` 的
    "最终读取" 同一模式），使「确认新事实」与「旧事实仍在检索中」不能同时成立。
    """
    db.flush()
    fresh = db.get(Memory, memory.id, populate_existing=True)
    if fresh is None or fresh.status != "active" or not _memory_is_current(fresh):
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆已被取代、已失效或已过期")
    return fresh


def supersede_memory(
    db: Session,
    *,
    memory_id: int,
    account_id: int,
    by_memory_id: int,
    reason: str = "user_replaced",
) -> Memory:
    """Mark ``memory_id`` as superseded by ``by_memory_id`` (both stay auditable).

    旧行**不删除**：`status` 保持 `active`，只写 `superseded_by_id`。这样：
    - 历史可审计、可回溯；
    - 用户可以撤销取代（`restore_memory`）；
    - 检索 eligibility 只排除被取代的行，不需要重建索引或删除引用。

    取代会失效该 Memory 的 RAG 文档（`index_superseded`，可恢复）：
    1. 文档级不可见即使在 eligibility 回退后仍然成立（纵深防御）；
    2. 且**不需要重建索引**——撤销取代时 `index_memory` 会验证完整 chunk 集
       并原地重新激活。
    """
    _require_memory_enabled(db)
    if reason not in MEMORY_SUPERSEDE_REASONS:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "取代原因不合法", {"reason": reason})
    old = db.get(Memory, memory_id)
    new = db.get(Memory, by_memory_id)
    if old is None or new is None:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
    if old.author_account_id != account_id or new.author_account_id != account_id:
        # 非本人记忆与不存在的记忆返回同一错误，不泄露存在性。
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
    if old.id == new.id:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆不能取代自己")
    for row in (old, new):
        if row.status != "active":
            raise_api_error(409, MEMORY_STATE_CONFLICT, "只能取代仍在生效的记忆")
        if row.confirmation_status != "confirmed":
            raise_api_error(409, MEMORY_STATE_CONFLICT, "只能取代已确认的记忆")
    # 「谁取代谁」是**用户的语义判断**，本函数不猜。唯一的机械判据是方向：
    # 不能用更早的事实取代更晚的事实（否则取代会变成静默的事实回退）。
    # 刻意**不**要求同一 source_id：manual 记忆没有来源身份，而「住上海」与
    # 「住苏州」通常来自不同消息（不同 source_id）却确实互相取代。要求来源相同
    # 只会挡住合法用法，不增加任何安全性质——被取代的行仍可审计、可恢复。
    if new.id <= old.id:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "取代方必须是更晚创建的记忆")
    if old.superseded_by_id is not None:
        if old.superseded_by_id == new.id:
            return old
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆已被其它版本取代")
    now = utcnow()
    if old.valid_to is None or old.valid_to > now:
        old.valid_to = now
    old.superseded_by_id = new.id
    old.supersede_reason = reason
    old.superseded_at = now
    old.restored_at = None
    old.updated_at = now
    _invalidate_memory_projection(db, old)
    db.flush()
    emit_domain_event(
        db,
        event_type="memory.superseded",
        aggregate_type="memory",
        aggregate_id=old.id,
        payload={
            "superseded_by": new.id,
            "reason": reason,
            "revision": old.revision,
        },
        space_id=old.space_id,
        actor_account_id=account_id,
    )
    db.flush()
    return old


def restore_memory(db: Session, *, memory_id: int, account_id: int) -> Memory:
    """Undo a supersede: the old fact becomes retrievable again.

    取代是可撤销的（用户改主意）。恢复需要：
    - 取代方**仍然存在且生效**，或已不存在——否则恢复会把一个指向空洞的指针
      留在行上，后续审计无法解释；
    - 与取代方重新确认来源仍然可读（否则旧事实恢复成「可检索」就绕过了来源失效）。

    恢复后重新索引旧 Memory：`index_memory` 会验证完整 chunk 集并把
    `index_superseded` 的文档原地激活，因此不需要全库重建。
    """
    _require_memory_enabled(db)
    memory = db.get(Memory, memory_id)
    if memory is None or memory.author_account_id != account_id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
    if memory.superseded_by_id is None:
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆当前未被取代")
    if memory.status != "active":
        raise_api_error(409, MEMORY_STATE_CONFLICT, "已撤销或已删除的记忆不能恢复")
    if not memory_sources.memory_materializable(db, memory):
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "原来源已失效或当前无权读取")
    now = utcnow()
    memory.superseded_by_id = None
    memory.supersede_reason = None
    memory.superseded_at = None
    # 恢复不等于「有效区间从未关闭」：把 valid_to 收回，使时间窗口回到「仍有效」。
    memory.valid_to = None
    memory.restored_at = now
    memory.updated_at = now
    db.flush()
    if platform_features.is_rag_enabled(db):
        index_memory(db, memory)
    emit_domain_event(
        db,
        event_type="memory.restored",
        aggregate_type="memory",
        aggregate_id=memory.id,
        payload={"revision": memory.revision},
        space_id=memory.space_id,
        actor_account_id=account_id,
    )
    db.flush()
    return memory


def _invalidate_memory_projection(db: Session, memory: Memory) -> None:
    """Tombstone the Memory projection with the recoverable reason.

    不用 `invalidate_source`：那个入口写的是 `source_invalidated`（永不复活），
    而取代是**可撤销**的，必须留下可恢复的标记。

    刻意**只动文档，不动 chunk**：chunk 的 `status` 属于**投影完整性证据**
    （`_chunks_match` 要求 active 才算「完整集」），不是授权输入。把 chunk 标成
    invalidated 会让撤销取代时的 `index_memory` 判定「旧投影缺少完整正文证据」而
    拒绝恢复（实测 409 `RAG_SOURCE_NOT_ALLOWED`）。可见性由文档状态（`d.status =
    'active'`）与检索层的取代过滤共同承担，两处都在。
    """
    rows = db.scalars(
        select(RAGDocument).where(
            RAGDocument.source_type == "memory",
            RAGDocument.source_id == str(memory.id),
            RAGDocument.status == "active",
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.status = "invalidated"
        row.invalidation_reason = "index_superseded"
        row.invalidated_at = now
        row.updated_at = now


def delete_memory(db: Session, *, memory_id: int, account_id: int) -> None:
    _require_memory_enabled(db)
    memory = db.get(Memory, memory_id)
    if memory is None or memory.author_account_id != account_id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
    if memory.status == "deleted":
        return
    now = utcnow()
    memory.status = "deleted"
    memory.deleted_at = now
    memory.updated_at = now
    invalidate_source(db, source_type="memory", source_id=str(memory.id))
    emit_domain_event(
        db,
        event_type="memory.deleted",
        aggregate_type="memory",
        aggregate_id=memory.id,
        payload={"status": memory.status},
        space_id=memory.space_id,
        actor_account_id=account_id,
    )


def revoke_memory(db: Session, *, memory_id: int, account_id: int) -> Memory:
    _require_memory_enabled(db)
    memory = db.get(Memory, memory_id)
    if memory is None or memory.author_account_id != account_id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
    if memory.status == "revoked":
        return memory
    if memory.status == "deleted":
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆已删除")
    now = utcnow()
    memory.status = "revoked"
    memory.revoked_at = now
    memory.revision += 1
    memory.updated_at = now
    invalidate_source(db, source_type="memory", source_id=str(memory.id))
    db.flush()
    emit_domain_event(
        db,
        event_type="memory.revoked",
        aggregate_type="memory",
        aggregate_id=memory.id,
        payload={"status": memory.status, "revision": memory.revision},
        space_id=memory.space_id,
        actor_account_id=account_id,
    )
    db.flush()
    return memory


def build_context(
    db: Session,
    *,
    run: AgentRun,
    actor: User,
    account: Account,
    query: str,
    token_budget: int = 2_000,
    policy_version: str | None = None,
    attempt: int | None = None,
) -> ContextProjection:
    """Build an auditable, budgeted data-only context from prefiltered hits.

    One attempt has exactly one valid build.  A repeated GET for the same
    (run, attempt) replays the stored build after re-authorizing every
    included source — it never generates a second competing build.  If any
    included source lost readability, the replay raises
    ``AGENT_CONTEXT_INVALIDATED`` and a new attempt must rebuild.
    """
    budget = max(1, min(token_budget, 32_000))
    space_id = run_session_space(db, run)
    provider = resolve_for_space(db, space_id)
    resolved_attempt = attempt if attempt is not None else run.attempt
    existing = db.scalar(
        select(ContextBuild)
        .where(ContextBuild.run_id == run.id, ContextBuild.attempt == resolved_attempt)
        .order_by(ContextBuild.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return _replay_context_build(db, existing, actor=actor, account=account, space_id=space_id)
    plan = plan_query(query)
    hits = search_rag(
        db,
        actor=actor,
        account=account,
        space_id=space_id,
        query=plan.normalized_query or query,
        agent_kind=run.kind,
        provider_kind=provider.kind,
        raise_on_restricted=True,
    )
    local_required = any(hit.sensitivity in ("high", "local_required") for hit in hits)
    if local_required and (provider.policy_result != "allowed" or provider.kind != "local"):
        raise_api_error(
            409,
            PROVIDER_LOCAL_REQUIRED_UNAVAILABLE,
            "敏感 Context 需要可用的本地 Provider",
        )
    build = ContextBuild(
        run_id=run.id,
        attempt=resolved_attempt,
        account_id=account.id,
        space_id=space_id,
        agent_kind=run.kind,
        query_hash=hashlib.sha256(query.encode()).hexdigest(),
        policy_version=policy_version or run.policy_version or config.POLICY_VERSION,
        token_budget=budget,
        created_at=utcnow(),
    )
    db.add(build)
    db.flush()
    blocks: list[ContextBlock] = []
    used = 0
    for rank, hit in enumerate(hits):
        estimate = hit.token_estimate
        include = used + estimate <= budget
        db.add(
            ContextBuildItem(
                build_id=build.id,
                source_type=hit.source_type,
                source_id=hit.source_id,
                citation_handle=hit.citation_handle,
                included=include,
                exclusion_reason=None if include else "token_budget",
                rank=rank if include else None,
                token_estimate=estimate,
                policy_version=policy_version or run.policy_version or config.POLICY_VERSION,
                metadata_json={
                    "scope": hit.scope,
                    "sensitivity": hit.sensitivity,
                    "trust": "data",
                    "chunk_id": hit.chunk_id,
                },
            )
        )
        if include:
            blocks.append(ContextBlock(hit.source_id, hit.text, estimate, hit.citation_handle))
            used += estimate
    # The authorized block payload is stored once so a replayed GET returns the
    # identical context instead of re-running a competing retrieval.
    build.blocks_json = [
        {
            "source_id": block.source_id,
            "text": block.text,
            "token_estimate": block.token_estimate,
            "citation_handle": block.citation_handle,
            "trust": block.trust,
        }
        for block in blocks
    ]
    db.flush()
    return ContextProjection(build.id, tuple(blocks), provider, local_required)


def _replay_context_build(
    db: Session,
    build: ContextBuild,
    *,
    actor: User,
    account: Account,
    space_id: int,
) -> ContextProjection:
    """Replay the attempt's stored build after re-authorizing each source."""
    from app.models.rag import RAG_SOURCE_TYPES

    blocks: list[ContextBlock] = []
    sensitivities: list[str] = []
    for block in build.blocks_json or []:
        document = db.scalar(
            select(RAGDocument).where(
                RAGDocument.source_type.in_(RAG_SOURCE_TYPES),
                RAGDocument.source_id == str(block["source_id"]),
                RAGDocument.status == "active",
            )
        )
        if document is None or not memory_sources.document_readable(
            db,
            document,
            actor=actor,
            account=account,
            space_id=space_id,
            agent_kind=build.agent_kind,
            # 重放没有记录 viewer 身份，因此无法为 steward build 证明私有读者 →
            # 传 NULL（fail-closed）。assistant build 的读者就是它的 account。
            private_reader_account_id=account.id if build.agent_kind == "assistant" else None,
        ):
            from app.errors import AGENT_CONTEXT_INVALIDATED

            raise_api_error(
                409,
                AGENT_CONTEXT_INVALIDATED,
                "先前构建的 context 来源已变化，需要新的 attempt 重建",
                {"context_build_id": build.id},
            )
        blocks.append(
            ContextBlock(
                source_id=str(block["source_id"]),
                text=str(block["text"]),
                token_estimate=int(block["token_estimate"]),
                citation_handle=str(block["citation_handle"]),
                trust=str(block.get("trust", "untrusted_data")),
            )
        )
        sensitivities.append(str(document.sensitivity))
    provider = resolve_for_space(db, space_id)
    local_required = any(value in ("high", "local_required") for value in sensitivities)
    return ContextProjection(build.id, tuple(blocks), provider, local_required)


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _document_materializable(db: Session, document: RAGDocument) -> bool:
    """Global source lifecycle, with no reader or membership impersonation."""
    return (
        document.status == "active"
        and document.invalidation_reason is None
        and document.revision == document.source_revision
        and memory_sources._document_chain(db, document) is not None
    )


def repair_fts(db: Session) -> int:
    """FTS physical repair only: rebuild the search projection from the
    currently legal chunk rows.  Never touches Memory/document business state,
    confirmation records or tombstones (D-R5 / D-AC6)."""
    _acquire_index_writer(db)
    db.flush()
    # PostgreSQL 上没有 FTS5 投影可修：词法检索由 PGroonga 承担，
    # 而 PGroonga 索引随行写入自动维护，不需要应用侧重建。
    if not _is_sqlite(db):
        return 0
    rebuilt = cursor = 0
    with db.begin_nested():
        _require_fresh_rag_enabled(db)
        db.execute(text("DELETE FROM rag_chunks_fts"))
        while True:
            documents = db.scalars(
                select(RAGDocument)
                .where(RAGDocument.id > cursor, RAGDocument.status == "active")
                .order_by(RAGDocument.id)
                .limit(100)
                .execution_options(populate_existing=True)
            ).all()
            if not documents:
                break
            for document in documents:
                cursor = document.id
                if not _document_materializable(db, document):
                    continue
                rows = [
                    row
                    for row in _version_chunks(db, document.id, document.index_version)
                    if row.status == "active" and row.source_revision == document.revision
                ]
                _repair_chunk_fts(db, rows)
                rebuilt += len(rows)
        _require_fresh_rag_enabled(db)
    return rebuilt


def ensure_memory_index(
    db: Session, memory: Memory, *, target_version: str | None = None
) -> RAGDocument:
    """Ensure every expected block and FTS row without changing activity/identity."""
    return index_memory(db, memory, target_version=target_version)


def rebuild_index(db: Session) -> int:
    """Deprecated combined entry kept for callers; now FTS repair only.

    Materialization of missing memories is owned by the bounded maintenance
    loop (``rag_maintenance.run_maintenance_batch``) — the old behavior here
    re-activated tombstoned documents (MR-25) and is intentionally gone.
    """
    return repair_fts(db)


def run_session_space(db: Session, run: AgentRun) -> int:
    from app.models.agent import AgentSession

    session = db.get(AgentSession, run.session_id)
    if session is None:
        raise_api_error(404, MEMORY_SCOPE_FORBIDDEN, "Agent 会话不存在")
    return session.space_id


__all__ = [
    "ContextBlock",
    "ContextProjection",
    "MemoryCandidateExtractor",
    "RAGHit",
    "build_context",
    "confirm_candidate",
    "delete_memory",
    "dismiss_candidate",
    "expire_due_memories",
    "index_memory",
    "ingest_authorized_document",
    "invalidate_source",
    "propose_candidate",
    "revoke_memory",
    "query_hash",
    "rebuild_index",
    "repair_fts",
    "ensure_memory_index",
]
