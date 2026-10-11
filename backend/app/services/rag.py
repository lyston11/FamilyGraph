"""Compatibility facade for the canonical scope-first RAG gateway."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.user import User
from app.services import knowledge_retrieval
from app.services.memory_rag import (
    RAGHit,
    invalidate_source,
    rebuild_index,
)
from app.services.memory_rag import (
    index_memory as ingest_memory,
)
from app.services.memory_rag import (
    ingest_authorized_document as ingest_document,
)


def search(
    db: Session,
    *,
    actor: User,
    space_id: int,
    query: str,
    agent_kind: str = "assistant",
    limit: int = 10,
) -> list[RAGHit]:
    account = db.scalar(select(Account).where(Account.user_id == actor.id))
    if account is None:
        return []
    return list(
        knowledge_retrieval.retrieve(
            db,
            knowledge_retrieval.RetrievalIdentity(
                agent_kind=agent_kind,
                actor=actor,
                account=account,
                space_id=space_id,
                source_types=knowledge_retrieval.CONTEXT_SOURCE_TYPES,
            ),
            query,
            limit=limit,
            for_model=False,
        ).hits
    )


__all__ = [
    "RAGHit",
    "ingest_document",
    "ingest_memory",
    "invalidate_source",
    "rebuild_index",
    "search",
]
