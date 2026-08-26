"""V2.5 memory, RAG and context-build persistence.

Revision ID: 0014_memory_rag
Revises: 0013_steward_action_card
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_memory_rag"
down_revision: str | None = "0013_steward_action_card"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "author_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_message_id",
            sa.Integer(),
            sa.ForeignKey("agent_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_document_ref", sa.String(255), nullable=True),
        sa.Column("source_span_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("suggested_scope", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(255), nullable=False),
        sa.Column("sensitivity", sa.String(16), nullable=False),
        sa.Column("extractor_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "confirmed_by_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("memory_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','dismissed','confirmed')", name="ck_memory_candidates_status"
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal','sensitive','high','local_required')",
            name="ck_memory_candidates_sensitivity",
        ),
        sa.CheckConstraint(
            "suggested_scope IN ('private','household','lineage')",
            name="ck_memory_candidates_scope",
        ),
        sa.CheckConstraint(
            "source_message_id IS NOT NULL OR source_document_ref IS NOT NULL",
            name="ck_memory_candidates_source",
        ),
    )
    op.create_index(
        "ix_memory_candidates_author_status", "memory_candidates", ["author_account_id", "status"]
    )

    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "author_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_candidate_id",
            sa.Integer(),
            sa.ForeignKey("memory_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_message_id",
            sa.Integer(),
            sa.ForeignKey("agent_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_document_ref", sa.String(255), nullable=True),
        sa.Column("source_span_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("raw_quote", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sensitivity", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(255), nullable=False),
        sa.Column("confirmation_status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retention_until", sa.DateTime(), nullable=True),
        sa.Column(
            "confirmed_by_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("scope IN ('private','household','lineage')", name="ck_memories_scope"),
        sa.CheckConstraint("status IN ('active','revoked','deleted')", name="ck_memories_status"),
        sa.CheckConstraint(
            "sensitivity IN ('normal','sensitive','high','local_required')",
            name="ck_memories_sensitivity",
        ),
        sa.CheckConstraint("confirmation_status = 'confirmed'", name="ck_memories_confirmation"),
        sa.CheckConstraint(
            "(scope = 'private' AND space_id IS NULL) OR "
            "(scope IN ('household','lineage') AND space_id IS NOT NULL)",
            name="ck_memories_scope_space",
        ),
        sa.CheckConstraint("length(trim(raw_quote)) > 0", name="ck_memories_source_quote"),
    )
    op.create_index("ix_memories_author_status", "memories", ["author_account_id", "status"])
    op.create_index("ix_memories_space_status", "memories", ["space_id", "status"])

    op.create_table(
        "rag_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column(
            "author_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("sensitivity", sa.String(16), nullable=False),
        sa.Column("confirmation_status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("visibility_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "visibility_snapshot_key",
            sa.String(128),
            nullable=False,
            server_default="visibility-v1",
        ),
        sa.Column("index_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('memory','family_story','authorized_document','profile',"
            "'public_kinship')",
            name="ck_rag_documents_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked','deleted','invalidated')", name="ck_rag_documents_status"
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal','sensitive','high','local_required')",
            name="ck_rag_documents_sensitivity",
        ),
        sa.CheckConstraint(
            "confirmation_status IN ('confirmed','authorized')",
            name="ck_rag_documents_confirmation",
        ),
        sa.CheckConstraint(
            "scope IN ('private','household','lineage','public')", name="ck_rag_documents_scope"
        ),
        sa.CheckConstraint(
            "(scope IN ('private','public') AND space_id IS NULL) OR "
            "(scope IN ('household','lineage') AND space_id IS NOT NULL)",
            name="ck_rag_documents_scope_space",
        ),
    )
    op.create_index("ix_rag_documents_scope", "rag_documents", ["space_id", "scope"])
    op.create_index(
        "ix_rag_documents_source", "rag_documents", ["source_type", "source_id", "revision"]
    )

    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("rag_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column(
            "embedding_status", sa.String(16), nullable=False, server_default="not_configured"
        ),
        sa.Column("index_version", sa.String(32), nullable=False, server_default="fts5-trigram-1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "embedding_status IN ('disabled','not_configured','pending','ready','failed')",
            name="ck_rag_chunks_embedding",
        ),
        sa.CheckConstraint(
            "status IN ('active','invalidated','deleted')", name="ck_rag_chunks_status"
        ),
    )
    op.create_index(
        "ix_rag_chunks_document", "rag_chunks", ["document_id", "chunk_index"], unique=True
    )
    op.execute(
        sa.text(
            "CREATE VIRTUAL TABLE rag_chunks_fts USING fts5("
            "chunk_id UNINDEXED, text, tokenize='trigram')"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN "
            "INSERT INTO rag_chunks_fts(rowid, chunk_id, text) "
            "VALUES (new.id, new.id, new.text); END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN "
            "DELETE FROM rag_chunks_fts WHERE rowid = old.id; END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER rag_chunks_au AFTER UPDATE OF text ON rag_chunks BEGIN "
            "DELETE FROM rag_chunks_fts WHERE rowid = old.id; "
            "INSERT INTO rag_chunks_fts(rowid, chunk_id, text) "
            "VALUES (new.id, new.id, new.text); END"
        )
    )

    op.create_table(
        "context_builds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_kind", sa.String(16), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_context_builds_run", "context_builds", ["run_id", "created_at"])
    op.create_table(
        "context_build_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "build_id",
            sa.Integer(),
            sa.ForeignKey("context_builds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("citation_handle", sa.String(255), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("exclusion_reason", sa.String(80), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_context_build_items_build", "context_build_items", ["build_id", "included"])


def downgrade() -> None:
    op.drop_index("ix_context_build_items_build", table_name="context_build_items")
    op.drop_table("context_build_items")
    op.drop_index("ix_context_builds_run", table_name="context_builds")
    op.drop_table("context_builds")
    op.execute(sa.text("DROP TRIGGER rag_chunks_au"))
    op.execute(sa.text("DROP TRIGGER rag_chunks_ad"))
    op.execute(sa.text("DROP TRIGGER rag_chunks_ai"))
    op.execute(sa.text("DROP TABLE rag_chunks_fts"))
    op.drop_index("ix_rag_chunks_document", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    op.drop_index("ix_rag_documents_source", table_name="rag_documents")
    op.drop_index("ix_rag_documents_scope", table_name="rag_documents")
    op.drop_table("rag_documents")
    op.drop_index("ix_memories_space_status", table_name="memories")
    op.drop_index("ix_memories_author_status", table_name="memories")
    op.drop_table("memories")
    op.drop_index("ix_memory_candidates_author_status", table_name="memory_candidates")
    op.drop_table("memory_candidates")
