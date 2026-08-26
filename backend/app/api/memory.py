"""Memory-card and scope-filtered RAG endpoints for authenticated users."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import config
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
from app.services import rag
from app.services.space_fsm import is_active_member

router = APIRouter(tags=["memory-rag"])


def _candidate_out(row: MemoryCandidate) -> MemoryCandidateOut:
    return MemoryCandidateOut.model_validate(row)


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
    )
    db.commit()
    return _candidate_out(row)


@router.get("/memory-candidates", response_model=list[MemoryCandidateOut])
def list_memory_candidates(
    include_decided: bool = Query(default=False),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[MemoryCandidateOut]:
    if not config.MEMORY_ENABLED:
        raise_api_error(503, MEMORY_DISABLED, "Memory 功能未开启")
    stmt = select(MemoryCandidate).where(MemoryCandidate.author_account_id == identity[1].id)
    if not include_decided:
        stmt = stmt.where(MemoryCandidate.status == "pending")
    rows = db.scalars(stmt.order_by(MemoryCandidate.id.desc())).all()
    return [_candidate_out(row) for row in rows]


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
    db.commit()
    return MemoryOut.model_validate(row)


@router.post("/memory-candidates/{candidate_id}/dismiss", response_model=MemoryCandidateOut)
def dismiss_memory_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemoryCandidateOut:
    row = memory_service.dismiss_candidate(db, candidate_id=candidate_id, account_id=identity[1].id)
    db.commit()
    return _candidate_out(row)


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(
    space_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[MemoryOut]:
    if not config.MEMORY_ENABLED:
        raise_api_error(503, MEMORY_DISABLED, "Memory 功能未开启")
    actor, account = identity
    if space_id is None:
        memory_service.expire_due_memories(db, account_id=account.id)
    else:
        if not is_active_member(db, space_id, actor.id):
            return []
        memory_service.expire_due_memories(db, account_id=account.id, space_id=space_id)
    db.commit()
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
    return [MemoryOut.model_validate(row) for row in rows]


@router.post("/memories/{memory_id}/revoke", response_model=MemoryOut)
def revoke_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> MemoryOut:
    row = memory_service.revoke_memory(db, memory_id=memory_id, account_id=identity[1].id)
    db.commit()
    return MemoryOut.model_validate(row)


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
    db.commit()
    return [RAGSearchOut(**row.__dict__) for row in rows]
