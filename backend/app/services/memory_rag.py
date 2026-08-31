"""V2.5 memory, scoped retrieval and context projection services.

The service has one important invariant: an AgentMessage can be a candidate
source, but it can never be ingested directly. Retrieval starts with a SQL
scope predicate and applies VisibilityPolicy once more before returning text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, text
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
from app.models.agent import AgentMessage, AgentRun, AgentSession
from app.models.context import ContextBuild, ContextBuildItem
from app.models.memory import MEMORY_SCOPES, SENSITIVITY_LEVELS, Memory, MemoryCandidate
from app.models.rag import RAG_SOURCE_TYPES, RAGChunk, RAGDocument
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.services import platform_roles, visibility
from app.services.agent_provider import ProviderResolution, resolve_for_space
from app.services.domain_events import emit as emit_domain_event
from app.utils.timeutil import utcnow

RAG_INDEX_VERSION = "fts5-trigram-v1"
_CANDIDATE_TEXT_LIMIT = 12_000
_SHARED_SCOPES = ("household", "lineage")


def _require_memory_enabled() -> None:
    if not config.MEMORY_ENABLED:
        raise_api_error(503, MEMORY_DISABLED, "Memory 功能未开启")


def _require_rag_enabled() -> None:
    if not config.RAG_ENABLED:
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
    source_quote: str,
    summary: str,
    suggested_scope: str,
    purpose: str,
    sensitivity: str = "normal",
    source_message_id: int | None = None,
    source_document_ref: str | None = None,
    extractor_version: str = "manual-v1",
) -> MemoryCandidate:
    """Persist a review card only; no RAG document is created here."""
    _require_memory_enabled()
    if not source_quote.strip() or not summary.strip():
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆候选原文和摘要不能为空")
    if len(source_quote) > _CANDIDATE_TEXT_LIMIT:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆候选原文超长")
    if source_message_id is None and not source_document_ref:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆候选必须关联原消息或授权文档")
    if source_message_id is not None:
        source_owner = db.scalar(
            select(AgentMessage.id)
            .join(AgentSession, AgentSession.id == AgentMessage.session_id)
            .where(
                AgentMessage.id == source_message_id,
                AgentSession.account_id == author_account_id,
            )
        )
        if source_owner is None:
            raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "只能引用本人会话中的原始消息")
    if suggested_scope not in MEMORY_SCOPES:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "建议 scope 不合法", {"scope": suggested_scope})
    _validate_sensitivity(sensitivity)
    now = utcnow()
    row = MemoryCandidate(
        author_account_id=author_account_id,
        source_message_id=source_message_id,
        source_document_ref=source_document_ref,
        source_quote=source_quote,
        summary=summary,
        suggested_scope=suggested_scope,
        purpose=purpose,
        sensitivity=sensitivity,
        extractor_version=extractor_version,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    emit_domain_event(
        db,
        event_type="memory.candidate.proposed",
        aggregate_type="memory_candidate",
        aggregate_id=row.id,
        payload={
            "source_message_id": source_message_id,
            "source_document_ref": source_document_ref,
        },
        actor_account_id=author_account_id,
    )
    return row


def create_candidate(
    db: Session,
    *,
    account: Account,
    source_span: dict[str, Any],
    raw_quote: str,
    summary: str,
    suggested_scope: str,
    purpose: str,
    sensitivity: str = "normal",
    source_message_id: int | None = None,
    source_document_ref: str | None = None,
) -> MemoryCandidate:
    row = propose_candidate(
        db,
        author_account_id=account.id,
        source_quote=raw_quote,
        summary=summary,
        suggested_scope=suggested_scope,
        purpose=purpose,
        sensitivity=sensitivity,
        source_message_id=source_message_id,
        source_document_ref=source_document_ref,
    )
    row.source_span_json = source_span
    return row


def _active_space_member(db: Session, *, user_id: int, space_id: int) -> bool:
    return (
        db.scalar(
            select(SpaceMember.id).where(
                SpaceMember.user_id == user_id,
                SpaceMember.space_id == space_id,
                SpaceMember.status == "active",
            )
        )
        is not None
    )


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
    _require_memory_enabled()
    candidate = db.get(MemoryCandidate, candidate_id)
    if candidate is None or candidate.author_account_id != confirmer_account.id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆候选不存在")
    if candidate.status != "pending":
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆候选已经处理")
    scope, space_id = _parse_memory_scope(scope, space_id)
    _validate_sensitivity(candidate.sensitivity)
    _validate_scope(scope, space_id)
    # P1 RAG session-space 绑定：来源会话属空间 A 的候选不得确认到空间 B；
    # private（本人）不受限。来源无会话（授权文档/手工）时不施加绑定。
    if scope in _SHARED_SCOPES and candidate.source_message_id is not None:
        source_space_id = db.scalar(
            select(AgentSession.space_id)
            .join(AgentMessage, AgentMessage.session_id == AgentSession.id)
            .where(AgentMessage.id == candidate.source_message_id)
        )
        if source_space_id is not None and space_id != source_space_id:
            raise_api_error(
                422,
                MEMORY_SCOPE_FORBIDDEN,
                "记忆候选来源会话空间与目标空间不一致",
                {"source_space_id": source_space_id, "target_space_id": space_id},
            )
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
    now = utcnow()
    if retention_days is not None:
        retention_until = now + timedelta(days=retention_days)
    memory = Memory(
        author_account_id=candidate.author_account_id,
        source_candidate_id=candidate.id,
        source_message_id=candidate.source_message_id,
        source_document_ref=candidate.source_document_ref,
        source_span_json=candidate.source_span_json,
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
    if config.RAG_ENABLED:
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


def dismiss_candidate(db: Session, *, candidate_id: int, account_id: int) -> MemoryCandidate:
    _require_memory_enabled()
    candidate = db.get(MemoryCandidate, candidate_id)
    if candidate is None or candidate.author_account_id != account_id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆候选不存在")
    if candidate.status != "pending":
        raise_api_error(409, MEMORY_STATE_CONFLICT, "记忆候选已经处理")
    candidate.status = "dismissed"
    candidate.decided_at = utcnow()
    candidate.updated_at = candidate.decided_at
    db.flush()
    emit_domain_event(
        db,
        event_type="memory.candidate.dismissed",
        aggregate_type="memory_candidate",
        aggregate_id=candidate.id,
        payload={"status": candidate.status},
        actor_account_id=account_id,
    )
    return candidate


def _chunk_text(value: str, size: int = 1200) -> list[str]:
    clean = value.strip()
    return [clean[pos : pos + size] for pos in range(0, len(clean), size)] or [""]


def index_memory(db: Session, memory: Memory) -> RAGDocument:
    """Create/update the sole RAG representation for a confirmed memory."""
    _require_rag_enabled()
    now = utcnow()
    document = db.scalar(
        select(RAGDocument).where(
            RAGDocument.source_type == "memory",
            RAGDocument.source_id == str(memory.id),
            RAGDocument.revision == memory.revision,
        )
    )
    if document is None:
        document = RAGDocument(
            source_type="memory",
            source_id=str(memory.id),
            author_account_id=memory.author_account_id,
            owner_user_id=db.scalar(
                select(Account.user_id).where(Account.id == memory.author_account_id)
            ),
            space_id=memory.space_id,
            scope=memory.scope,
            sensitivity=memory.sensitivity,
            confirmation_status="confirmed",
            source_revision=memory.revision,
            revision=memory.revision,
            visibility_snapshot_key=f"memory:{memory.id}:r{memory.revision}",
            index_version=RAG_INDEX_VERSION,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(document)
        db.flush()
    else:
        document.status = "active"
        document.updated_at = now
        db.query(RAGChunk).filter(RAGChunk.document_id == document.id).delete(
            synchronize_session=False
        )
    for index, chunk in enumerate(_chunk_text(memory.content)):
        db.add(
            RAGChunk(
                document_id=document.id,
                chunk_index=index,
                source_revision=memory.revision,
                text=chunk,
                token_estimate=max(1, len(chunk) // 4),
                created_at=now,
            )
        )
    db.flush()
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
    _require_rag_enabled()
    if source_type not in RAG_SOURCE_TYPES or source_type == "memory":
        raise_api_error(422, RAG_SOURCE_NOT_ALLOWED, "该来源类型不能通过文档入口索引")
    if not text_value.strip():
        raise_api_error(422, RAG_SOURCE_NOT_ALLOWED, "可索引文档不能为空")
    _validate_rag_scope(scope, space_id)
    _validate_sensitivity(sensitivity)
    if scope == "public" and sensitivity in ("high", "local_required"):
        raise_api_error(422, MEMORY_SENSITIVE_SCOPE_FORBIDDEN, "高敏感文档不能公开到全局")
    now = utcnow()
    document = RAGDocument(
        source_type=source_type,
        source_id=source_id,
        author_account_id=author_account_id,
        owner_user_id=(
            db.scalar(select(Account.user_id).where(Account.id == author_account_id))
            if author_account_id is not None
            else None
        ),
        space_id=space_id,
        scope=scope,
        sensitivity=sensitivity,
        confirmation_status="authorized",
        source_revision=revision,
        revision=revision,
        visibility_snapshot_key=visibility_snapshot_key,
        index_version=RAG_INDEX_VERSION,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()
    for index, chunk in enumerate(_chunk_text(text_value)):
        db.add(
            RAGChunk(
                document_id=document.id,
                chunk_index=index,
                source_revision=revision,
                text=chunk,
                token_estimate=max(1, len(chunk) // 4),
                created_at=now,
            )
        )
    db.flush()
    emit_domain_event(
        db,
        event_type="rag.document.ingested",
        aggregate_type="rag_document",
        aggregate_id=document.id,
        payload={"source_type": source_type, "scope": scope, "revision": revision},
        space_id=space_id,
        actor_account_id=author_account_id,
    )
    return document


def _fts_match(value: str) -> str:
    # Match as one quoted phrase. This prevents FTS operators from changing the
    # query while retaining trigram matching for CJK and short text.
    return '"' + value.replace('"', '""') + '"'


def _author_visible(db: Session, actor: User, document: RAGDocument, space_id: int) -> bool:
    if document.author_account_id is None:
        # Authorless documents retain their existing public-assistant semantics for
        # ordinary family identities. A platform operator is never a family-data
        # identity, even when a membership row happens to grant SQL scope access.
        return not platform_roles.is_platform_operator(db, actor.account)
    author = db.scalar(
        select(User)
        .join(Account, Account.user_id == User.id)
        .where(Account.id == document.author_account_id)
    )
    if author is None:
        return False
    return visibility.evaluate(
        db, actor, author, space_context=space_id, purpose=visibility.PURPOSE_RAG
    ).visible


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
) -> list[RAGHit]:
    """Search with SQL scope/confirmation/status predicates before FTS results escape."""
    _require_rag_enabled()
    clean_query = query.strip()
    if not clean_query:
        return []
    if not _active_space_member(db, user_id=actor.id, space_id=space_id):
        return []
    expire_due_memories(db, account_id=account.id, space_id=space_id)
    limit = max(1, min(limit, 100))
    if raise_on_restricted and provider_kind != "local":
        restricted_hits = search_rag(
            db,
            actor=actor,
            account=account,
            space_id=space_id,
            query=clean_query,
            agent_kind=agent_kind,
            limit=limit,
            provider_kind="local",
        )
        if any(hit.sensitivity in ("high", "local_required") for hit in restricted_hits):
            raise_api_error(
                409,
                PROVIDER_LOCAL_REQUIRED_UNAVAILABLE,
                "敏感 Context 需要可用的本地 Provider",
            )
    # Restricted material is eligible only when the selected provider is local.
    sensitivity_predicate = (
        "AND d.sensitivity IN ('normal','sensitive')" if provider_kind != "local" else ""
    )
    # never starts with an unrestricted similarity result set.
    sql = text(
        f"""
        SELECT c.id AS chunk_id, d.id AS document_id, d.source_type, d.source_id, c.text,
               c.token_estimate, d.scope, d.sensitivity, d.revision,
               bm25(rag_chunks_fts) AS rank
        FROM rag_chunks_fts
        JOIN rag_chunks AS c ON c.id = rag_chunks_fts.rowid
        JOIN rag_documents AS d ON d.id = c.document_id
        WHERE rag_chunks_fts MATCH :match
          AND c.status = 'active'
          AND d.status = 'active'
            AND d.confirmation_status IN ('confirmed', 'authorized')
          {sensitivity_predicate}
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
        ORDER BY rank ASC, c.id ASC
        LIMIT :limit
        """
    )
    rows = db.execute(
        sql,
        {
            "match": _fts_match(clean_query),
            "account_id": account.id,
            "user_id": actor.id,
            "space_id": space_id,
            "is_assistant": int(agent_kind == "assistant"),
            "limit": limit,
        },
    ).mappings()
    hits: list[RAGHit] = []
    for row in rows:
        document_id = int(row["document_id"])
        document = db.get(RAGDocument, document_id)
        if document is None or not _author_visible(db, actor, document, space_id):
            continue
        hits.append(
            RAGHit(
                document_id=document_id,
                chunk_id=int(row["chunk_id"]),
                source_id=str(row["source_id"]),
                text=str(row["text"]),
                token_estimate=int(row["token_estimate"]),
                rank=float(row["rank"]),
                citation_handle=f"rag:{row['source_id']}:r{row['revision']}:c{row['chunk_id']}",
                scope=str(row["scope"]),
                sensitivity=str(row["sensitivity"]),
                revision=int(row["revision"]),
                source_type=str(row["source_type"]),
                index_version=RAG_INDEX_VERSION,
            )
        )
    return hits


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
    _require_memory_enabled()
    memory = db.get(Memory, memory_id)
    if memory is None or memory.author_account_id != account_id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
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
    _require_memory_enabled()
    memory = db.get(Memory, memory_id)
    if memory is None or memory.author_account_id != account_id:
        raise_api_error(404, MEMORY_CANDIDATE_NOT_FOUND, "记忆不存在")
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
) -> ContextProjection:
    """Build an auditable, budgeted data-only context from prefiltered hits."""
    budget = max(1, min(token_budget, 32_000))
    space_id = run_session_space(db, run)
    provider = resolve_for_space(db, space_id)
    hits = search_rag(
        db,
        actor=actor,
        account=account,
        space_id=space_id,
        query=query,
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
        include = used + hit.token_estimate <= budget
        db.add(
            ContextBuildItem(
                build_id=build.id,
                source_type=hit.source_type,
                source_id=hit.source_id,
                citation_handle=hit.citation_handle,
                included=include,
                exclusion_reason=None if include else "token_budget",
                rank=rank if include else None,
                token_estimate=hit.token_estimate,
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
            blocks.append(
                ContextBlock(hit.source_id, hit.text, hit.token_estimate, hit.citation_handle)
            )
            used += hit.token_estimate
    db.flush()
    return ContextProjection(build.id, tuple(blocks), provider, local_required)


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def rebuild_index(db: Session) -> int:
    """Rebuild FTS and materialize active memories missing an index document."""
    _require_rag_enabled()
    active_memories = db.scalars(select(Memory).where(Memory.status == "active")).all()
    for memory in active_memories:
        document = db.scalar(
            select(RAGDocument).where(
                RAGDocument.source_type == "memory",
                RAGDocument.source_id == str(memory.id),
                RAGDocument.revision == memory.revision,
                RAGDocument.status == "active",
            )
        )
        if document is None:
            index_memory(db, memory)
    db.execute(text("DELETE FROM rag_chunks_fts"))
    db.execute(
        text(
            "INSERT INTO rag_chunks_fts(rowid, chunk_id, text) "
            "SELECT id, id, text FROM rag_chunks "
            "WHERE status = 'active' AND document_id IN "
            "(SELECT id FROM rag_documents WHERE status = 'active')"
        )
    )
    return int(
        db.scalar(
            text(
                "SELECT count(*) FROM rag_chunks "
                "WHERE status = 'active' AND document_id IN "
                "(SELECT id FROM rag_documents WHERE status = 'active')"
            )
        )
        or 0
    )


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
]
