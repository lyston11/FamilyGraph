"""Memory-card and scope-filtered RAG endpoints for authenticated users."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import MEMORY_DISABLED, raise_api_error
from app.models.account import Account
from app.models.memory import Memory, MemoryCandidate
from app.models.user import User
from app.schemas.memory import (
    MemoryCandidateCreate,
    MemoryCandidateOut,
    MemoryConfirmRequest,
    MemoryOut,
    RAGSearchOut,
)
from app.services import memory as memory_service
from app.services import memory_sources, platform_features, rag
from app.services.space_fsm import is_active_member

router = APIRouter(tags=["memory-rag"])


def _candidate_out(
    db: Session, row: MemoryCandidate, identity: tuple[User, Account]
) -> MemoryCandidateOut:
    access = memory_sources.source_access(db, row, actor=identity[0], account=identity[1])
    return MemoryCandidateOut.model_validate(
        dict(
            id=row.id,
            source_message_id=row.source_message_id if access.readable else None,
            source_document_ref=row.source_document_ref if access.readable else None,
            source_span_json=memory_sources.public_source_snapshot(row) if access.readable else {},
            source_kind=row.source_kind,
            source_status=access.status,
            allowed_scopes=list(access.allowed_scopes),
            raw_quote=row.source_quote if access.readable else None,
            summary=row.summary if access.readable else None,
            purpose=row.purpose if access.readable else None,
            suggested_scope=row.suggested_scope,
            sensitivity=row.sensitivity,
            extractor_version=row.extractor_version,
            status=row.status,
            memory_id=row.memory_id,
            created_at=row.created_at,
            decided_at=row.decided_at,
        )
    )


def _memory_out(
    db: Session,
    row: Memory,
    identity: tuple[User, Account],
    space_id: int | None = None,
) -> MemoryOut:
    access = memory_sources.memory_access(
        db, row, actor=identity[0], account=identity[1], space_id=space_id
    )
    return MemoryOut.model_validate(
        dict(
            id=row.id,
            source_candidate_id=row.source_candidate_id,
            source_message_id=row.source_message_id if access.readable else None,
            source_document_ref=row.source_document_ref if access.readable else None,
            source_span_json=memory_sources.public_source_snapshot(row) if access.readable else {},
            source_kind=row.source_kind,
            source_status=access.status,
            allowed_scopes=list(access.allowed_scopes),
            raw_quote=row.raw_quote if access.readable else None,
            content=row.content if access.readable else None,
            purpose=row.purpose if access.readable else None,
            scope=row.scope,
            space_id=row.space_id,
            sensitivity=row.sensitivity,
            confirmation_status=row.confirmation_status,
            revision=row.revision,
            retention_until=row.retention_until,
            status=row.status,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    )


@router.post("/memory-candidates", status_code=201, response_model=MemoryCandidateOut)
def create_memory_candidate(
    body: MemoryCandidateCreate,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemoryCandidateOut:
    row = memory_service.create_candidate(
        db,
        account=identity[1],
        source_span=body.source_span,
        raw_quote=body.raw_quote,
        summary=body.summary,
        suggested_scope=body.suggested_scope,
        purpose=body.purpose,
        sensitivity=body.sensitivity,
        source_message_id=body.source_message_id,
        source_document_ref=body.source_document_ref,
        source=body.source.model_dump() if body.source is not None else None,
        idempotency_key=body.idempotency_key,
    )
    db.flush()
    result = _candidate_out(db, row, identity)
    result.model_dump_json()
    db.commit()
    return result


@router.get("/memory-candidates", response_model=list[MemoryCandidateOut])
def list_memory_candidates(
    include_decided: bool = Query(default=False),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[MemoryCandidateOut]:
    if not platform_features.is_memory_enabled(db):
        raise_api_error(503, MEMORY_DISABLED, "Memory 功能未开启")
    stmt = select(MemoryCandidate).where(MemoryCandidate.author_account_id == identity[1].id)
    if not include_decided:
        stmt = stmt.where(MemoryCandidate.status == "pending")
    rows = db.scalars(stmt.order_by(MemoryCandidate.id.desc())).all()
    return [_candidate_out(db, row, identity) for row in rows]


@router.post("/memory-candidates/{candidate_id}/confirm", response_model=MemoryOut)
def confirm_memory_candidate(
    candidate_id: int,
    body: MemoryConfirmRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemoryOut:
    row = memory_service.confirm_candidate(
        db,
        candidate_id=candidate_id,
        confirmer=identity[0],
        confirmer_account=identity[1],
        scope=body.scope,
        retention_days=body.retention_days,
    )
    db.flush()
    result = _memory_out(db, row, identity)
    result.model_dump_json()
    db.commit()
    return result


@router.post("/memory-candidates/{candidate_id}/dismiss", response_model=MemoryCandidateOut)
def dismiss_memory_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemoryCandidateOut:
    row = memory_service.dismiss_candidate(db, candidate_id=candidate_id, account_id=identity[1].id)
    db.flush()
    result = _candidate_out(db, row, identity)
    result.model_dump_json()
    db.commit()
    return result


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(
    space_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[MemoryOut]:
    if not platform_features.is_memory_enabled(db):
        raise_api_error(503, MEMORY_DISABLED, "Memory 功能未开启")
    actor, account = identity
    if space_id is None:
        memory_service.expire_due_memories(db, account_id=account.id)
    else:
        if not is_active_member(db, space_id, actor.id):
            return []
        memory_service.expire_due_memories(db, account_id=account.id, space_id=space_id)
    stmt = select(Memory).where(
        Memory.status == "active", Memory.confirmation_status == "confirmed"
    )
    private = (Memory.scope == "private") & (Memory.author_account_id == account.id)
    if space_id is None:
        stmt = stmt.where(private)
    else:
        shared = (Memory.space_id == space_id) & Memory.scope.in_(("household", "lineage"))
        stmt = stmt.where(or_(private, shared))
    rows = db.scalars(stmt.order_by(Memory.id.desc())).all()
    result = []
    for row in rows:
        projected = _memory_out(db, row, identity, space_id)
        # Only the owner can see quarantined metadata; other readers receive no row.
        if row.author_account_id == account.id or projected.source_status in (
            "available",
            "deleted_snapshot",
        ):
            projected.model_dump_json()
            result.append(projected)
    db.commit()
    return result


@router.post("/memories/{memory_id}/revoke", response_model=MemoryOut)
def revoke_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemoryOut:
    row = memory_service.revoke_memory(db, memory_id=memory_id, account_id=identity[1].id)
    db.flush()
    result = _memory_out(db, row, identity)
    result.model_dump_json()
    db.commit()
    return result


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    memory_service.delete_memory(db, memory_id=memory_id, account_id=identity[1].id)
    db.commit()
    return Response(status_code=204)


@router.get("/rag/search", response_model=list[RAGSearchOut])
def search_rag(
    space_id: int = Query(gt=0),
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[RAGSearchOut]:
    rows = rag.search(db, actor=identity[0], space_id=space_id, query=q, limit=limit)
    result = [RAGSearchOut(**row.__dict__) for row in rows]
    for item in result:
        item.model_dump_json()
    db.commit()
    return result
