"""D: RAG index lifecycle — invalidation reason, versioned chunk identity,
bounded maintenance state.

Revision ID: 0045_rag_index_lifecycle
Revises: 0044_rag_citation_contract, 0044_steward_terminology
Create Date: 2026-09-14

- rag_documents.invalidation_reason distinguishes source_invalidated (never
  resurrectable) from index_superseded (recoverable after source/content validation).
  Existing invalidated rows are stamped source_invalidated: the historical
  tombstone path never recorded an index supersede.
- rag_chunks unique key becomes (document_id, index_version, chunk_index) so
  two index versions of one document can coexist while the activity pointer
  decides recallability.
- rag_index_maintenance_state / rag_index_maintenance_failures persist the
  bounded backfill cursor, lease fence and retry ledger.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0045_rag_index_lifecycle"
down_revision: tuple[str, str] | None = (
    "0044_rag_citation_contract",
    "0044_steward_terminology",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rag_documents",
        sa.Column("invalidation_reason", sa.String(length=32), nullable=True),
    )
    op.execute(
        "UPDATE rag_documents SET invalidation_reason = 'source_invalidated' "
        "WHERE status != 'active'"
    )
    op.drop_index("ix_rag_chunks_document", table_name="rag_chunks")
    op.create_index(
        "ix_rag_chunks_document_version",
        "rag_chunks",
        ["document_id", "index_version", "chunk_index"],
        unique=True,
    )
    op.create_table(
        "rag_index_maintenance_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cursor_memory_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "rag_index_maintenance_failures",
        sa.Column(
            "memory_id",
            sa.Integer(),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=False),
        sa.Column("last_error_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.execute(
        "INSERT INTO rag_index_maintenance_state (id, policy_version, updated_at) "
        "SELECT 1, 'rag-index-maint-v1', CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM rag_index_maintenance_state WHERE id = 1)"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE rag_documents SET id = id WHERE 0"))
    incompatible = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
            "WHERE c.index_version != d.index_version"
        )
    )
    collisions = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM (SELECT document_id, chunk_index FROM rag_chunks "
            "GROUP BY document_id, chunk_index HAVING COUNT(*) > 1)"
        )
    )
    reasons = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM rag_documents WHERE invalidation_reason IS NOT NULL "
            "AND invalidation_reason != 'source_invalidated'"
        )
    )
    if incompatible or collisions or reasons:
        dependencies = connection.scalar(
            sa.text("SELECT COUNT(*) FROM memories WHERE source_kind = 'rag_chunk'")
        )
        raise RuntimeError(
            "Cannot losslessly downgrade RAG lifecycle: "
            f"historical_chunks={incompatible}, key_collisions={collisions}, "
            f"distinct_reasons={reasons}, saved_dependencies={dependencies}; "
            "retain chunks and roll forward"
        )
    op.drop_table("rag_index_maintenance_failures")
    op.drop_table("rag_index_maintenance_state")
    op.drop_index("ix_rag_chunks_document_version", table_name="rag_chunks")
    op.create_index(
        "ix_rag_chunks_document",
        "rag_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )
    op.drop_column("rag_documents", "invalidation_reason")
