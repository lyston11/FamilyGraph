"""Canonical RAG identity, complete-input evidence and fenced scan watermarks.

Revision ID: 0047_rag_lifecycle_integrity
Revises: 0046_context_execution_contract

All changes are in place. In particular, do not batch-rebuild rag_documents:
SQLite ON DELETE CASCADE would destroy chunks (including saved dependencies)
when foreign_keys is enabled on the actual Alembic connection.
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "0047_rag_lifecycle_integrity"
down_revision = "0046_context_execution_contract"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _preflight() -> None:
    connection = op.get_bind()
    # Serialize preflight and constraint creation, including SQLite's legacy
    # transaction mode, without changing a row or rebuilding a parent table.
    connection.execute(sa.text("UPDATE rag_documents SET id = id WHERE 0"))
    duplicates = (
        connection.execute(
            sa.text(
                "SELECT source_type, revision, GROUP_CONCAT(id) AS document_ids, COUNT(*) AS n "
                "FROM rag_documents GROUP BY source_type, source_id, revision HAVING COUNT(*) > 1"
            )
        )
        .mappings()
        .all()
    )
    mirrors = (
        connection.execute(
            sa.text(
                "SELECT id, revision, source_revision FROM rag_documents "
                "WHERE revision != source_revision ORDER BY id"
            )
        )
        .mappings()
        .all()
    )
    report = {
        "duplicate_groups": len(duplicates),
        "duplicates": [dict(row) for row in duplicates[:50]],
        "revision_conflicts": len(mirrors),
        "conflicts": [dict(row) for row in mirrors[:50]],
        "unknown_tombstones": connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM rag_documents WHERE status != 'active' AND "
                "(invalidation_reason IS NULL OR invalidation_reason NOT IN "
                "('source_invalidated', 'index_superseded'))"
            )
        ),
        "saved_rag_dependencies": connection.scalar(
            sa.text("SELECT COUNT(*) FROM memories WHERE source_kind = 'rag_chunk'")
        ),
    }
    # IDs/counts/revisions only: source_id may contain a human-supplied label.
    logger.info("RAG lifecycle preflight: %s", json.dumps(report, sort_keys=True))
    if duplicates or mirrors:
        raise RuntimeError(
            "RAG canonical identity conflict; no schema/data changes made; "
            "review the metadata report before retrying"
        )


def upgrade() -> None:
    _preflight()
    op.add_column("rag_documents", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.drop_index("ix_rag_documents_source", table_name="rag_documents")
    op.create_index(
        "ix_rag_documents_source",
        "rag_documents",
        ["source_type", "source_id", "revision"],
        unique=True,
    )
    # Equivalent to the model CHECK, without a destructive SQLite table copy.
    for operation, suffix in (
        ("INSERT", "insert"),
        ("UPDATE OF revision, source_revision", "update"),
    ):
        op.execute(
            f"CREATE TRIGGER rag_documents_revision_{suffix} BEFORE {operation} "
            "ON rag_documents WHEN NEW.revision != NEW.source_revision BEGIN "
            "SELECT RAISE(ABORT, 'rag document revision mirror conflict'); END"
        )
    for column in (
        sa.Column("upper_memory_id", sa.Integer(), nullable=True),
        sa.Column("cursor_document_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upper_document_id", sa.Integer(), nullable=True),
        sa.Column("stage_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "target_index_version", sa.String(32), nullable=False, server_default="fts5-trigram-v2"
        ),
    ):
        op.add_column("rag_index_maintenance_state", column)
    op.execute(
        "UPDATE rag_index_maintenance_state SET policy_version = 'rag-index-maint-v2', "
        "cursor_memory_id = 0, attempt = attempt + 1, lease_owner = NULL, "
        "lease_expires_at = NULL WHERE policy_version = 'rag-index-maint-v1'"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE rag_documents SET id = id WHERE 0"))
    evidence = connection.scalar(
        sa.text("SELECT COUNT(*) FROM rag_documents WHERE content_sha256 IS NOT NULL")
    )
    other_target = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM rag_index_maintenance_state "
            "WHERE target_index_version != 'fts5-trigram-v2' "
            "OR policy_version NOT IN ('rag-index-maint-v1', 'rag-index-maint-v2')"
        )
    )
    if evidence or other_target:
        raise RuntimeError(
            "Cannot discard RAG integrity evidence/target policy; retain data and roll forward"
        )
    op.execute("DROP TRIGGER rag_documents_revision_update")
    op.execute("DROP TRIGGER rag_documents_revision_insert")
    op.drop_index("ix_rag_documents_source", table_name="rag_documents")
    op.create_index(
        "ix_rag_documents_source", "rag_documents", ["source_type", "source_id", "revision"]
    )
    op.drop_column("rag_documents", "content_sha256")
    for name in (
        "target_index_version",
        "stage_round",
        "upper_document_id",
        "cursor_document_id",
        "upper_memory_id",
    ):
        op.drop_column("rag_index_maintenance_state", name)
    op.execute(
        "UPDATE rag_index_maintenance_state SET policy_version = 'rag-index-maint-v1', "
        "cursor_memory_id = 0, attempt = attempt + 1, lease_owner = NULL, lease_expires_at = NULL "
        "WHERE policy_version = 'rag-index-maint-v2'"
    )
