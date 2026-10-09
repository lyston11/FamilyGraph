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
from app.models.checks import DialectCheck

MEMORY_CANDIDATE_STATUSES = ("pending", "dismissed", "confirmed")
MEMORY_SCOPES = ("private", "household", "lineage")
MEMORY_STATUSES = ("active", "revoked", "deleted")
# 取代原因集合。`user_replaced` = 用户在确认新候选时显式取代；`source_revision`
# = 同一来源产生了新 revision，旧快照不再代表当前事实。`expired` 留给保留期到期
# 路径，目前不使用（到期仍走 deleted 终态）。
MEMORY_SUPERSEDE_REASONS = ("user_replaced", "source_revision", "expired")
SENSITIVITY_LEVELS = ("normal", "sensitive", "high", "local_required")
MEMORY_SOURCE_KINDS = ("manual", "agent_message", "rag_chunk", "legacy")
# 前置条件在两方言上完全相同；只有 JSON 部分需要分派。
_SNAPSHOT_PREFIX = (
    "source_verification = 'unverified' OR (source_kind != 'legacy' "
    "AND source_type IS NOT NULL AND source_id IS NOT NULL "
    "AND source_revision IS NOT NULL AND source_revision > 0 "
)


# SQLite 的 json_extract 是类型敏感的（数字 1 相等、字符串 "1" 不等），
# PostgreSQL 用 jsonb 对 jsonb 比较保留同样的类型敏感语义。写成 `->> ... ::int`
# 会把字符串 "1" 也判为相等，那是**不同**的约束。
def source_snapshot_check(name: str) -> DialectCheck:
    """同一判据的两个方言表达式；`name` 由调用方给（两张表各有约束名）。"""
    return DialectCheck(
        name=name,
        sqlite_expr=(
            _SNAPSHOT_PREFIX
            + "AND coalesce(json_extract(source_span_json, '$.version') = 1, 0) "
            + "AND coalesce(json_extract(source_span_json, '$.kind') = source_kind, 0))"
        ),
        postgres_expr=(
            _SNAPSHOT_PREFIX
            + "AND coalesce((source_span_json::jsonb -> 'version') = '1'::jsonb, false) "
            + "AND coalesce((source_span_json::jsonb ->> 'kind') = source_kind, false))"
        ),
    )


def _check_in(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


class MemoryCandidate(Base):
    """A user-reviewable proposal; it is never directly searchable."""

    __tablename__ = "memory_candidates"
    __table_args__ = (
        _check_in("status", MEMORY_CANDIDATE_STATUSES, "ck_memory_candidates_status"),
        _check_in("sensitivity", SENSITIVITY_LEVELS, "ck_memory_candidates_sensitivity"),
        _check_in("suggested_scope", MEMORY_SCOPES, "ck_memory_candidates_scope"),
        _check_in("source_kind", MEMORY_SOURCE_KINDS, "ck_memory_candidates_source_kind"),
        _check_in(
            "source_verification",
            ("verified", "unverified"),
            "ck_memory_candidates_source_verification",
        ),
        source_snapshot_check("ck_memory_candidates_source"),
        Index("ix_memory_candidates_author_status", "author_account_id", "status"),
        Index("uq_memory_candidates_request", "author_account_id", "idempotency_key", unique=True),
        Index("ix_memory_candidates_source", "source_type", "source_id", "source_revision"),
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
    source_kind: Mapped[str] = mapped_column(String(16), default="legacy", server_default="legacy")
    source_verification: Mapped[str] = mapped_column(
        String(16), default="unverified", server_default="unverified"
    )
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Durable provenance, not a live FK: deletion must not erase the original scope.
    source_space_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        _check_in("source_kind", MEMORY_SOURCE_KINDS, "ck_memories_source_kind"),
        _check_in(
            "source_verification", ("verified", "unverified"), "ck_memories_source_verification"
        ),
        source_snapshot_check("ck_memories_source"),
        Index("ix_memories_author_status", "author_account_id", "status"),
        Index("ix_memories_space_status", "space_id", "status"),
        Index("uq_memories_source_candidate", "source_candidate_id", unique=True),
        Index("ix_memories_source", "source_type", "source_id", "source_revision"),
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
    source_kind: Mapped[str] = mapped_column(String(16), default="legacy", server_default="legacy")
    source_verification: Mapped[str] = mapped_column(
        String(16), default="unverified", server_default="unverified"
    )
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_space_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmation_request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
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
    # ---- 时间有效区间与取代指针（P1 记忆更新语义）----
    #
    # 被取代的记忆**不删除**：历史可审计、可回溯，也可撤销取代（清空
    # `superseded_by_id`）。检索 eligibility 只排除「被取代」的行，因此这个指针
    # 是**承重**的：清空它就会让旧事实重新进入模型上下文。
    #
    # 这六列刻意不带数据库 CHECK：SQLite 上新增 CHECK 需要整表重建（复制全表），
    # 而判据（不自我指向、reason 枚举、时间区间有序）由服务层在同一事务内校验，
    # 收益不抵迁移风险。
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supersede_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    "MEMORY_SUPERSEDE_REASONS",
    "Memory",
    "MemoryCandidate",
    "SENSITIVITY_LEVELS",
]
