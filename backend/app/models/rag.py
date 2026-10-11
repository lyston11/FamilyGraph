"""Allow-listed RAG knowledge metadata and searchable chunks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
INDEXED_SOURCE_TYPES: tuple[str, ...] = ("memory", "authorized_document", "public_kinship")

#: 声明但无写入方的类别 → 理由。「声明了但没人写」是**显式决定**，不是偶然状态。
#:
#: 不删这些枚举值：`RAG_SOURCE_TYPES` 参与 `ck_rag_documents_source_type` 的 CHECK
#: 约束，删除需要 SQLite 整表重建（复制全表），收益不抵迁移风险，且会让历史数据的
#: source_type 变成不可读的值。
UNINDEXED_SOURCE_TYPES: dict[str, str] = {
    "family_story": "依赖尚不存在的家族故事写入功能；本任务只登记合同",
    "profile": "逐 viewer 求值（visibility.evaluate 依赖读者），走 get_profile_summary 结构化投影",
}

#: **谁可以读哪些来源类别**——唯一真源。
#:
#: ## 为什么必须有这张表
#:
#: 在它之前，三个消费者共用 `knowledge_retrieval.CONTEXT_SOURCE_TYPES`，而那个常量的值是
#: `None`，语义是「全部已声明类别」。后果有两层：
#:
#: 1. **默认方向是放开**——新增 `source_type` 会静默进入每一个复用了该常量的消费者；
#: 2. **常量名描述单一消费者却被多处复用**——`ContextBuilder.build()` 同时服务 assistant
#:    与 steward（它按 `agent_kind` 分支），却无论哪种 kind 都拿到同一份「全部」来源集。
#:    于是 steward 的上下文能读到 `authorized_document`（该类别的检索门是 grant 存在性，
#:    **没有** `:is_assistant` 门），比 steward 自己的工具策略 `("memory",)` 更宽。
#:
#: 现在每个消费者显式列出自己读什么。新增类别**不会**自动进入任何一行——这是刻意的：
#: 放宽一个读取面必须是可评审的代码改动，而不是一次枚举值新增。
#:
#: ## 为什么按 (agent_kind, 面) 命名，而不是按模块名
#:
#: `assistant_context` / `steward_context` 是两个不同的键，因为它们的**授权强度不同**，
#: 而不是因为它们恰好在不同文件里。同理 `browser_search`（`GET /rag/search`）与
#: `assistant_context` 今天取值相同，但消费者不同：将来调整 agent 上下文的来源面不应连带
#: 改变用户自己看到的搜索结果。跨消费者复用同一常量正是本表要消除的形态。
#:
#: ## 刻意的非对称
#:
#: `public_kinship` 在 `*_context` 与 `browser_search` 中，但**不在** `assistant_memory_tool`
#: 中。`search_memory` 的名字是「记忆」，返回空间外的通用称谓知识会误导模型把语料当成
#: 用户记忆（2026-10-10 实测到 `term-pack:zh-CN` 出现在该工具结果里）。上下文构建是拼装
#: 材料，通用知识作为背景是合理的。
RAG_CONSUMER_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "assistant_context": ("memory", "authorized_document", "public_kinship"),
    "steward_context": ("memory",),
    "assistant_memory_tool": ("memory", "authorized_document"),
    "steward_memory_tool": ("memory",),
    "browser_search": ("memory", "authorized_document", "public_kinship"),
}

#: agent kind → 上下文构建的消费者键。
_CONTEXT_CONSUMER_BY_KIND: dict[str, str] = {
    "assistant": "assistant_context",
    "steward": "steward_context",
}


def context_source_types(agent_kind: str) -> tuple[str, ...]:
    """按 agent kind 取上下文来源集。未知 kind **fail closed**，不回退到最宽集合。

    与 `knowledge_retrieval.resolve_authorization` 对未知 kind 抛 422 的行为一致：
    一个我们认不出的身份，不应该拿到任何一种可读集。
    """
    key = _CONTEXT_CONSUMER_BY_KIND.get(agent_kind)
    if key is None:
        raise ValueError(f"unknown agent_kind for RAG context: {agent_kind!r}")
    return RAG_CONSUMER_SOURCE_TYPES[key]


def validate_consumer_source_types(
    policy: Mapping[str, Sequence[str]],
) -> list[str]:
    """校验一张消费者来源策略表，返回错误列表（空 = 通过）。

    纯函数，因此可以用**伪造的策略**做反证：没有反证，这个校验器就只是同义反复。

    四条判据，每条都对应一种真实笔误：

    - key 非空：空 key 让「谁在读」不可追溯；
    - 值非空：空集意味着该消费者读不到任何东西——那是 `resolve_authorization` 的
      授权结论，不该由来源策略表达（两者混在一起会看不出是配置错还是授权拒绝）；
    - 值 ⊆ `RAG_SOURCE_TYPES`：防拼写错误（`public_kinship` 写成 `public_kin`）；
    - 值 ⊆ `INDEXED_SOURCE_TYPES` 且 ∩ `UNINDEXED_SOURCE_TYPES` = ∅：
      防把**没有写入方**的类别写进策略——那会让该消费者以为自己能读到一类永不存在的语料。
    """
    errors: list[str] = []
    declared = set(RAG_SOURCE_TYPES)
    indexed = set(INDEXED_SOURCE_TYPES)
    unindexed = set(UNINDEXED_SOURCE_TYPES)
    for key, values in policy.items():
        if not str(key).strip():
            errors.append("consumer key must be a non-empty name")
            continue
        if not values:
            errors.append(f"{key}: consumer must declare at least one source type")
            continue
        unknown = sorted(set(values) - declared)
        if unknown:
            errors.append(f"{key}: undeclared source types {unknown}")
        not_indexed = sorted(set(values) & unindexed)
        if not_indexed:
            errors.append(f"{key}: source types have no writer and are not indexed {not_indexed}")
        missing = sorted(set(values) - indexed - unindexed)
        if missing:
            errors.append(f"{key}: source types are neither indexed nor explained {missing}")
    return errors


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
    "RAG_CONSUMER_SOURCE_TYPES",
    "RAGChunk",
    "RAGDocument",
    "RAGIndexMaintenanceFailure",
    "RAGIndexMaintenanceState",
    "RAG_DOCUMENT_STATUSES",
    "RAG_SENSITIVITIES",
    "RAG_SOURCE_TYPES",
    "UNINDEXED_SOURCE_TYPES",
    "context_source_types",
    "validate_consumer_source_types",
]
