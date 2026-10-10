"""Allow-listed RAG knowledge metadata and searchable chunks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

RAG_SOURCE_TYPES = ("memory", "family_story", "authorized_document", "profile", "public_kinship")

#: 有**真实写入方**的 source_type：在生产执行路径上会被创建。
#:
#: 与 `RAG_SOURCE_TYPES` 分开登记的理由是这两件事曾经被混为一谈：声明了五类，实际
#: 只有一类有写入方，而「所有 `RAG_SOURCE_TYPES` 都已登记 tier 份额」的断言照样通过
#: ——它只检查份额存在，不检查写入方存在。空转因此既无文档也无测试保护。
#:
#: 差集必须在 `UNINDEXED_SOURCE_TYPES` 里逐条给出理由。新增或删除 `RAG_SOURCE_TYPES`
#: 而不更新两侧时，`tests/test_rag_source_type_registry.py` 会失败。
INDEXED_SOURCE_TYPES: tuple[str, ...] = ("memory", "public_kinship")

#: 声明但无写入方的类别 → 理由。「声明了但没人写」是**显式决定**，不是偶然状态。
#:
#: 不删这些枚举值：`RAG_SOURCE_TYPES` 参与 `ck_rag_documents_source_type` 的 CHECK
#: 约束，删除需要 SQLite 整表重建（复制全表），收益不抵迁移风险，且会让历史数据的
#: source_type 变成不可读的值。
UNINDEXED_SOURCE_TYPES: dict[str, str] = {
    "family_story": "依赖尚不存在的家族故事写入功能；本任务只登记合同",
    "authorized_document": "附件授权缺段落级粒度，整篇授权等于交给空间全体成员；待独立任务",
    "profile": "逐 viewer 求值（visibility.evaluate 依赖读者），走 get_profile_summary 结构化投影",
}
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
        CheckConstraint("revision = source_revision", name="ck_rag_documents_revision_mirror"),
        Index("ix_rag_documents_scope", "space_id", "scope"),
        Index("ix_rag_documents_source", "source_type", "source_id", "revision", unique=True),
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
    # Hash of the complete indexing input (Memory.content, not raw_quote).
    # NULL means legacy/unproven; a partial legacy projection cannot sign it.
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Distinguishes a source-level tombstone (never resurrectable) from an
    # index supersede (recoverable only after source/content revalidation).
    invalidation_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
        Index(
            "ix_rag_chunks_document_version",
            "document_id",
            "index_version",
            "chunk_index",
            unique=True,
        ),
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


class RAGIndexMaintenanceState(Base):
    """Singleton cursor/lease row for bounded RAG index backfill rounds."""

    __tablename__ = "rag_index_maintenance_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    cursor_memory_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    upper_memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cursor_document_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    upper_document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    target_index_version: Mapped[str] = mapped_column(
        String(32), default="fts5-trigram-v2", nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RAGIndexMaintenanceFailure(Base):
    """Bounded retry ledger: source id + stable error code + backoff, no text."""

    __tablename__ = "rag_index_maintenance_failures"

    memory_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_error_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "INDEXED_SOURCE_TYPES",
    "RAGChunk",
    "RAGDocument",
    "RAGIndexMaintenanceFailure",
    "RAGIndexMaintenanceState",
    "RAG_DOCUMENT_STATUSES",
    "RAG_SENSITIVITIES",
    "RAG_SOURCE_TYPES",
    "UNINDEXED_SOURCE_TYPES",
]
