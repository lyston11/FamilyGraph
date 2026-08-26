"""V2.5 reviewable memory candidates and confirmed memories.

Candidates are deliberately separate from confirmed memories and have no RAG
relationship.  Only an explicit confirmation command may create searchable
knowledge from a candidate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

MEMORY_CANDIDATE_STATUSES = ("pending", "dismissed", "confirmed")
MEMORY_SCOPES = ("private", "household", "lineage")
MEMORY_STATUSES = ("active", "revoked", "deleted")
SENSITIVITY_LEVELS = ("normal", "sensitive", "high", "local_required")


def _check_in(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


class MemoryCandidate(Base):
    """A user-reviewable proposal; it is never directly searchable."""

    __tablename__ = "memory_candidates"
    __table_args__ = (
        _check_in("status", MEMORY_CANDIDATE_STATUSES, "ck_memory_candidates_status"),
        _check_in("sensitivity", SENSITIVITY_LEVELS, "ck_memory_candidates_sensitivity"),
        _check_in("suggested_scope", MEMORY_SCOPES, "ck_memory_candidates_scope"),
        CheckConstraint(
            "source_message_id IS NOT NULL OR source_document_ref IS NOT NULL",
            name="ck_memory_candidates_source",
        ),
        Index("ix_memory_candidates_author_status", "author_account_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL"), nullable=True
    )
    source_document_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_span_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    confirmed_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Memory(Base):
    """Confirmed user-controlled knowledge with explicit scope and lifecycle."""

    __tablename__ = "memories"
    __table_args__ = (
        _check_in("scope", MEMORY_SCOPES, "ck_memories_scope"),
        _check_in("status", MEMORY_STATUSES, "ck_memories_status"),
        _check_in("sensitivity", SENSITIVITY_LEVELS, "ck_memories_sensitivity"),
        _check_in("confirmation_status", ("confirmed",), "ck_memories_confirmation"),
        CheckConstraint(
            "(scope = 'private' AND space_id IS NULL) OR "
            "(scope IN ('household','lineage') AND space_id IS NOT NULL)",
            name="ck_memories_scope_space",
        ),
        CheckConstraint("length(trim(raw_quote)) > 0", name="ck_memories_source_quote"),
        Index("ix_memories_author_status", "author_account_id", "status"),
        Index("ix_memories_space_status", "space_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_candidates.id", ondelete="SET NULL"), nullable=True
    )
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL"), nullable=True
    )
    source_document_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_span_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw_quote: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmation_status: Mapped[str] = mapped_column(
        String(16), default="confirmed", nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "MEMORY_CANDIDATE_STATUSES",
    "MEMORY_SCOPES",
    "MEMORY_STATUSES",
    "Memory",
    "MemoryCandidate",
    "SENSITIVITY_LEVELS",
]
