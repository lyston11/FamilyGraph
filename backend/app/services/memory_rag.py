"""V2.5 memory, scoped retrieval and context projection services.

The service has one important invariant: an AgentMessage can be a candidate
source, but it can never be ingested directly. Retrieval starts with a SQL
scope predicate and applies VisibilityPolicy once more before returning text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, bindparam, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    MEMORY_CANDIDATE_NOT_FOUND,
    MEMORY_DISABLED,
    MEMORY_SCOPE_FORBIDDEN,
    MEMORY_SENSITIVE_SCOPE_FORBIDDEN,
    MEMORY_STATE_CONFLICT,
    PROVIDER_LOCAL_REQUIRED_UNAVAILABLE,
    RAG_DISABLED,
    RAG_SOURCE_NOT_ALLOWED,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import AgentRun
from app.models.context import ContextBuild, ContextBuildItem
from app.models.memory import MEMORY_SCOPES, SENSITIVITY_LEVELS, Memory, MemoryCandidate
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAG_SOURCE_TYPES, RAGChunk, RAGDocument
from app.models.space import FamilySpace
from app.models.user import User
from app.services import memory_sources, platform_features
from app.services.agent_provider import ProviderResolution, resolve_for_space
from app.services.domain_events import emit as emit_domain_event
from app.services.policy_consumer import is_policy_consumer_kind
from app.services.rag_budget import estimate_tokens
from app.services.rag_query import QueryPlan, plan_query
from app.utils.timeutil import utcnow

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

    version = "candidate-extractor-v1"

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
        for item in self._detector(conversation_text):
            rows.append(
                propose_candidate(
                    db,
                    author_account_id=author_account_id,
                    source_message_id=item.source_message_id or source_message_id,
                    source_document_ref=item.source_document_ref,
                    source_quote=item.source_quote,
                    summary=item.summary,
                    suggested_scope=item.suggested_scope,
                    purpose=item.purpose,
                    sensitivity=item.sensitivity,
                    extractor_version=self.version,
                )
            )
        return rows

    @staticmethod
    def _default_detector(text_value: str) -> list[MemoryCandidateInput]:
        """Return no implicit candidates by default.

        Product-specific extraction is an explicit opt-in detector. This keeps
        ordinary chat from silently becoming a durable memory source.
        """
        del text_value
        return []


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
    """Acquire the SQLite writer before refreshing mutable sources or flags.

    A no-op UPDATE also starts a real transaction before any SAVEPOINT on
    SQLite's legacy driver. Callers own the short outer commit/rollback.
    """
    db.execute(text("UPDATE rag_documents SET id = id WHERE 0"))


def _require_fresh_rag_enabled(db: Session) -> None:
    db.flush()
    db.get(PlatformFeatureConfig, 1, populate_existing=True)
    _require_rag_enabled(db)


def _fresh_materializable_memory(db: Session, memory_id: int) -> Memory:
    db.flush()
    memory = db.get(Memory, memory_id, populate_existing=True)
    if memory is None or not memory_sources.memory_materializable(db, memory):
        _index_conflict("来源未验证或已失效，不能建立索引")
    assert memory is not None
    return memory


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


def _fts_rows(db: Session, rows: Sequence[RAGChunk]) -> dict[int, tuple[Any, str]]:
    if not rows:
        return {}
    records = db.execute(
        text("SELECT rowid, chunk_id, text FROM rag_chunks_fts WHERE rowid IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": [row.id for row in rows]},
    )
    return {record[0]: (record[1], record[2]) for record in records}


def _repair_chunk_fts(db: Session, rows: Sequence[RAGChunk]) -> None:
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


def index_memory(db: Session, memory: Memory, *, target_version: str | None = None) -> RAGDocument:
    """Ensure the canonical projection; existing activity pointers never move here."""
    _acquire_index_writer(db)
    db.flush()
    with db.begin_nested():
        _require_fresh_rag_enabled(db)
        current = _fresh_materializable_memory(db, memory.id)
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
        current = _fresh_materializable_memory(db, current.id)
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


# SQL eligibility predicates shared by the FTS path and the short-word fallback
# so a two-character query can never reach raw rows the FTS path cannot.
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
  ))
  {sensitivity}
  AND (
    (d.scope = 'private' AND d.author_account_id = :account_id AND :is_assistant = 1)
    OR
    (d.scope IN ('household', 'lineage') AND d.space_id = :space_id
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


def _rows_to_hits(
    db: Session,
    rows: Any,
    *,
    actor: User,
    account: Account,
    space_id: int,
    agent_kind: str,
    rank_by_order: bool,
) -> tuple[list[RAGHit], int]:
    """Project rows through the visibility policy once more; count denials."""
    hits: list[RAGHit] = []
    denied = 0
    for position, row in enumerate(rows):
        document_id = int(row["document_id"])
        document = db.get(RAGDocument, document_id)
        if document is None or not memory_sources.document_readable(
            db,
            document,
            actor=actor,
            account=account,
            space_id=space_id,
            agent_kind=agent_kind,
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
) -> list[RAGHit]:
    """Search with SQL scope/confirmation/status predicates before results escape.

    Retrieval is planned (``rag_query.plan_query``): the FTS branch ORs the
    exact phrase and bounded terms; two-character Chinese terms that cannot
    trigram-match take a parameterized LIKE fallback restricted to the same
    eligibility predicates with a bounded scan budget.
    """
    _require_rag_enabled(db)
    if not is_policy_consumer_kind(agent_kind):
        raise_api_error(422, MEMORY_SCOPE_FORBIDDEN, "policy consumer 不受支持")
    # Steward is a shared-data policy consumer only.  The SQL predicates below
    # intentionally use is_assistant for private/public branches, so it can
    # never read private memory or unrestricted public material.
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
        "account_id": account.id,
        "user_id": actor.id,
        "space_id": space_id,
        "is_assistant": is_assistant,
    }
    eligibility = _ELIGIBILITY_SQL.format(sensitivity=sensitivity_predicate)

    hits: list[RAGHit] = []
    seen_chunk_ids: set[int] = set()
    scanned = 0
    denied = 0

    def collect(sql: Any, branch_params: dict[str, Any], *, rank_by_order: bool) -> None:
        nonlocal scanned, denied
        offset = 0
        while len(hits) < limit and scanned < _FALLBACK_SCAN_LIMIT:
            page_size = min(_SCAN_PAGE_SIZE, _FALLBACK_SCAN_LIMIT - scanned)
            rows = (
                db.execute(sql, {**branch_params, "limit": page_size, "offset": offset})
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
            )
            denied += rejected
            for hit in page:
                if hit.chunk_id in seen_chunk_ids:
                    continue
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
                seen_chunk_ids.add(hit.chunk_id)
                hits.append(hit)
                if len(hits) == limit:
                    break
            if len(rows) < page_size:
                break

    # Each branch has stable SQL ordering; both share one candidate budget.
    # Policy rejection refills from the next page instead of exhausting the
    # caller's result limit. The bound counts returned SQL candidates, not the
    # database engine's internal index/table operations.
    match_values = ([_fts_match(plan.phrase)] if plan.phrase else []) + [
        _fts_match(term) for term in plan.fts_terms
    ]
    if match_values:
        sql = text(f"""
            SELECT c.id AS chunk_id, d.id AS document_id, d.source_type, d.source_id, c.text,
                   c.token_estimate, d.scope, d.sensitivity, d.revision, c.index_version,
                   c.chunk_index, c.source_revision, bm25(rag_chunks_fts) AS rank
            FROM rag_chunks_fts
            JOIN rag_chunks AS c ON c.id = rag_chunks_fts.rowid
            JOIN rag_documents AS d ON d.id = c.document_id
            WHERE rag_chunks_fts MATCH :match AND {eligibility}
            ORDER BY rank ASC, c.id ASC LIMIT :limit OFFSET :offset
        """)
        collect(sql, {**params, "match": " OR ".join(match_values)}, rank_by_order=False)
    if len(hits) < limit and plan.fallback_terms and scanned < _FALLBACK_SCAN_LIMIT:
        clauses = " OR ".join(
            f"c.text LIKE :like{idx} ESCAPE '!'" for idx in range(len(plan.fallback_terms))
        )
        # Parameters prevent SQL injection; LIKE's pattern characters still
        # need escaping so a literal underscore cannot match unrelated text.
        escaped_terms = [
            term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            for term in plan.fallback_terms
        ]
        fallback_params = {
            **params,
            **{f"like{idx}": f"%{term}%" for idx, term in enumerate(escaped_terms)},
        }
        sql = text(
            _HIT_SQL.format(condition=f"({clauses})", eligibility=eligibility, ordering="c.id ASC")
        )
        collect(sql, fallback_params, rank_by_order=True)
    if trace is not None:
        trace.update(
            {
                "scanned": scanned,
                "denied": denied,
                "returned": len(hits),
                "stop_reason": "limit"
                if len(hits) >= limit
                else "scan_limit"
                if scanned >= _FALLBACK_SCAN_LIMIT
                else "exhausted",
            }
        )
    return hits


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
