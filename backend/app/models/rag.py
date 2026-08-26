"""Allow-listed RAG knowledge metadata and searchable chunks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

RAG_SOURCE_TYPES = ("memory", "family_story", "authorized_document", "profile", "public_kinship")
RAG_DOCUMENT_STATUSES = ("active", "revoked", "deleted", "invalidated")
RAG_SENSITIVITIES = ("normal", "sensitive", "high", "local_required")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class RAGDocument(Base):
    """Authorized, confirmation-backed material eligible for retrieval."""

    def __init__(self, **kwargs: Any) -> None:
        if "source_revision" in kwargs and "revision" not in kwargs:
            kwargs["revision"] = kwargs["source_revision"]
        if "revision" in kwargs and "source_revision" not in kwargs:
            kwargs["source_revision"] = kwargs["revision"]
        super().__init__(**kwargs)

    __tablename__ = "rag_documents"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('memory','family_story','authorized_document','profile',"
            "'public_kinship')",
            name="ck_rag_documents_source_type",
        ),
        CheckConstraint(
            "status IN ('active','revoked','deleted','invalidated')",
            name="ck_rag_documents_status",
        ),
        CheckConstraint(
            "sensitivity IN ('normal','sensitive','high','local_required')",
            name="ck_rag_documents_sensitivity",
        ),
        CheckConstraint(
            "confirmation_status IN ('confirmed','authorized')",
            name="ck_rag_documents_confirmation",
        ),
        CheckConstraint(
            "scope IN ('private','household','lineage','public')", name="ck_rag_documents_scope"
        ),
        CheckConstraint(
            "(scope IN ('private','public') AND space_id IS NULL) OR "
            "(scope IN ('household','lineage') AND space_id IS NOT NULL)",
            name="ck_rag_documents_scope_space",
        ),
        Index("ix_rag_documents_scope", "space_id", "scope"),
        Index("ix_rag_documents_source", "source_type", "source_id", "revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    author_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    visibility_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    visibility_snapshot_key: Mapped[str] = mapped_column(
        String(128), nullable=False, default="visibility-v1"
    )
    index_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RAGChunk(Base):
    """Searchable chunk with an independent lifecycle guard."""

    __tablename__ = "rag_chunks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','invalidated','deleted')", name="ck_rag_chunks_status"
        ),
        CheckConstraint(
            "embedding_status IN ('disabled','not_configured','pending','ready','failed')",
            name="ck_rag_chunks_embedding",
        ),
        Index("ix_rag_chunks_document", "document_id", "chunk_index", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("rag_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_status: Mapped[str] = mapped_column(
        String(16), default="not_configured", nullable=False
    )
    index_version: Mapped[str] = mapped_column(
        String(32), default="fts5-trigram-v1", nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow_naive)


__all__ = [
    "RAGChunk",
    "RAGDocument",
    "RAG_DOCUMENT_STATUSES",
    "RAG_SENSITIVITIES",
    "RAG_SOURCE_TYPES",
]
