"""Compatibility facade for the canonical V2.5 memory/RAG service."""

from app.services.memory_rag import (
    MemoryCandidateExtractor,
    build_context,
    confirm_candidate,
    create_candidate,
    delete_memory,
    dismiss_candidate,
    expire_due_memories,
    index_memory,
    ingest_authorized_document,
    invalidate_for_domain_event,
    invalidate_source,
    propose_candidate,
    revoke_memory,
    search_rag,
)

__all__ = [
    "MemoryCandidateExtractor",
    "build_context",
    "confirm_candidate",
    "create_candidate",
    "delete_memory",
    "dismiss_candidate",
    "expire_due_memories",
    "index_memory",
    "ingest_authorized_document",
    "invalidate_for_domain_event",
    "invalidate_source",
    "propose_candidate",
    "revoke_memory",
    "search_rag",
]
