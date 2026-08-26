"""Compatibility exports for the canonical V2.5 HTTP contracts.

The authoritative request/response models live in :mod:`app.schemas.memory`.
Keep this module as a stable import path for integrations while preventing a
second, divergent Memory/RAG schema from being introduced.
"""

from app.schemas.memory import (
    MemoryCandidateCreate,
    MemoryCandidateOut,
    MemoryConfirmRequest,
    MemoryOut,
    RAGSearchOut,
)

__all__ = [
    "MemoryCandidateCreate",
    "MemoryCandidateOut",
    "MemoryConfirmRequest",
    "MemoryOut",
    "RAGSearchOut",
]
