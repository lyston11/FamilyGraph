"""Explicit memory provenance, request idempotency and atomic confirmation.

Unknown historical labels remain unverified. Only an exact, original user
message with matching ownership is upgraded automatically. The source snapshot
survives FK SET NULL; original content and confirmation history are preserved.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0042_memory_source_contract"
down_revision: str | None = "0041_term_pack_expansion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_CHECK = (
    "source_verification = 'unverified' OR (source_kind != 'legacy' "
    "AND source_type IS NOT NULL AND source_id IS NOT NULL "
    "AND source_revision IS NOT NULL AND source_revision > 0 "
    "AND coalesce(json_extract(source_span_json, '$.version') = 1, 0) "
    "AND coalesce(json_extract(source_span_json, '$.kind') = source_kind, 0))"
)
_SOURCE_NAMES = (
    "source_kind",
    "source_verification",
    "source_type",
    "source_id",
    "source_revision",
    "source_space_id",
)


def _source_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("source_kind", sa.String(16), nullable=False, server_default="legacy"),
        sa.Column(
            "source_verification", sa.String(16), nullable=False, server_default="unverified"
        ),
        sa.Column("source_type", sa.String(32), nullable=True),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("source_revision", sa.Integer(), nullable=True),
        # Audit identity, intentionally independent of live space FKs.
        sa.Column("source_space_id", sa.Integer(), nullable=True),
    ]


def _source_checks(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            "source_kind IN ('manual','agent_message','rag_chunk','legacy')",
            name=sa.schema.conv(f"ck_{table}_ck_{table}_source_kind"),
        ),
        sa.CheckConstraint(
            "source_verification IN ('verified','unverified')",
            name=sa.schema.conv(f"ck_{table}_ck_{table}_source_verification"),
        ),
        sa.CheckConstraint(_SOURCE_CHECK, name=sa.schema.conv(f"ck_{table}_ck_{table}_source")),
    ]


def _backfill_original_messages(table: str, quote_column: str) -> None:
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(f"""
        SELECT r.id, r.author_account_id, r.source_message_id, r.source_document_ref,
               r.source_span_json, r.{quote_column} AS quote,
               m.role, m.content_json, m.created_at AS message_created_at,
               s.id AS session_id, s.account_id AS session_account_id,
               s.space_id AS session_space_id
        FROM {table} r
        LEFT JOIN agent_messages m ON m.id = r.source_message_id
        LEFT JOIN agent_sessions s ON s.id = m.session_id
    """)
        )
        .mappings()
        .all()
    )
    verified = 0
    for row in rows:
        if (
            row["source_document_ref"] is not None
            or row["role"] != "user"
            or row["session_account_id"] != row["author_account_id"]
        ):
            continue
        try:
            payload = json.loads(row["content_json"])
        except (ValueError, TypeError):
            continue
        if (
            not isinstance(payload, dict)
            or set(payload) != {"text"}
            or not isinstance(payload.get("text"), str)
            or not payload["text"].strip()
            or row["quote"] != payload["text"]
        ):
            continue
        if table == "memories":
            scope_row = (
                bind.execute(
                    sa.text("SELECT scope, space_id FROM memories WHERE id=:id"), {"id": row["id"]}
                )
                .mappings()
                .one()
            )
            if scope_row["scope"] != "private" and scope_row["space_id"] != row["session_space_id"]:
                continue
        snapshot = {
            "version": 1,
            "kind": "agent_message",
            "message_id": row["source_message_id"],
            "session_id": row["session_id"],
            "session_space_id": row["session_space_id"],
            "author_account_id": row["author_account_id"],
            "message_created_at": str(row["message_created_at"]),
            "quote_sha256": hashlib.sha256(row["quote"].encode("utf-8")).hexdigest(),
        }
        old_span = json.loads(row["source_span_json"])
        if old_span:
            snapshot["legacy_source_span"] = old_span
        bind.execute(
            sa.text(f"""
            UPDATE {table} SET source_kind='agent_message', source_verification='verified',
                source_type='agent_message', source_id=:source_id, source_revision=1,
                source_space_id=:space_id, source_span_json=:snapshot WHERE id=:id
        """),
            {
                "source_id": str(row["source_message_id"]),
                "space_id": row["session_space_id"],
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
                "id": row["id"],
            },
        )
        verified += 1
    logging.getLogger("alembic.runtime.migration").info(
        "%s source audit: verified=%d, unverified=%d (content and history preserved)",
        table,
        verified,
        len(rows) - verified,
    )


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text("""
        SELECT source_candidate_id, count(*) AS total FROM memories
        WHERE source_candidate_id IS NOT NULL GROUP BY source_candidate_id HAVING count(*) > 1
        ORDER BY source_candidate_id LIMIT 20
    """)
    ).all()
    if duplicates:
        raise RuntimeError(
            "Memory confirmation duplicates require explicit repair; "
            f"candidate IDs/counts (first 20): {duplicates!r}; no rows were removed"
        )
    # Rebuilding a referenced SQLite parent with foreign_keys=ON would SET NULL
    # every Memory.source_candidate_id. Stage the child data without FKs and
    # recreate both tables in dependency order under a savepoint instead.
    metadata = sa.MetaData()
    memory_table = sa.Table("memories", metadata, autoload_with=bind)
    original_columns = [column.name for column in memory_table.columns]
    candidate_checks = {
        item["name"] for item in sa.inspect(bind).get_check_constraints("memory_candidates")
    }
    old_source_check = next(
        (
            name
            for name in (
                "ck_memory_candidates_ck_memory_candidates_source",
                "ck_memory_candidates_source",
            )
            if name in candidate_checks
        ),
        None,
    )
    if old_source_check is None:
        raise RuntimeError(
            "Unknown memory candidate source constraint; inspect schema before migration"
        )
    bind.exec_driver_sql("SAVEPOINT memory_source_upgrade")
    try:
        bind.exec_driver_sql(
            "CREATE TEMP TABLE memory_source_upgrade_data AS SELECT * FROM memories"
        )
        op.drop_table("memories")
        with op.batch_alter_table("memory_candidates", recreate="always") as batch:
            batch.drop_constraint(op.f(old_source_check), type_="check")
            for column in _source_columns():
                batch.add_column(column)
            batch.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
            batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
            batch.add_column(sa.Column("confirmation_fingerprint", sa.String(64), nullable=True))
            for constraint in _source_checks("memory_candidates"):
                batch.create_check_constraint(op.f(str(constraint.name)), constraint.sqltext)
            batch.create_index(
                "uq_memory_candidates_request",
                ["author_account_id", "idempotency_key"],
                unique=True,
            )
            batch.create_index(
                "ix_memory_candidates_source", ["source_type", "source_id", "source_revision"]
            )
        for column in _source_columns():
            memory_table.append_column(column)
        memory_table.append_column(
            sa.Column("confirmation_request_json", sa.JSON(), nullable=False, server_default="{}")
        )
        for constraint in _source_checks("memories"):
            memory_table.append_constraint(constraint)
        sa.Index("uq_memories_source_candidate", memory_table.c.source_candidate_id, unique=True)
        sa.Index(
            "ix_memories_source",
            memory_table.c.source_type,
            memory_table.c.source_id,
            memory_table.c.source_revision,
        )
        memory_table.create(bind)
        columns = ", ".join(original_columns)
        bind.exec_driver_sql(
            f"INSERT INTO memories ({columns}) SELECT {columns} FROM memory_source_upgrade_data"
        )
        bind.exec_driver_sql("DROP TABLE memory_source_upgrade_data")
        _backfill_original_messages("memory_candidates", "source_quote")
        _backfill_original_messages("memories", "raw_quote")
        violations = bind.exec_driver_sql("PRAGMA foreign_key_check").all()
        if violations:
            raise RuntimeError(
                f"Memory source migration foreign-key violations: {violations[:20]!r}"
            )
        bind.exec_driver_sql("RELEASE SAVEPOINT memory_source_upgrade")
    except BaseException:
        bind.exec_driver_sql("ROLLBACK TO SAVEPOINT memory_source_upgrade")
        bind.exec_driver_sql("RELEASE SAVEPOINT memory_source_upgrade")
        raise


def downgrade() -> None:
    bind = op.get_bind()
    count = int(
        bind.scalar(
            sa.text(
                "SELECT (SELECT count(*) FROM memories) + "
                "(SELECT count(*) FROM memory_candidates)"
            )
        )
        or 0
    )
    if count:
        raise RuntimeError(
            "Memory source downgrade would discard provenance/history; "
            "keep the data and forward-fix, or make an explicit data decision"
        )
    with op.batch_alter_table("memories", recreate="always") as batch:
        batch.drop_index("uq_memories_source_candidate")
        batch.drop_index("ix_memories_source")
        for suffix in ("source", "source_kind", "source_verification"):
            batch.drop_constraint(op.f(f"ck_memories_ck_memories_{suffix}"), type_="check")
        for name in (*_SOURCE_NAMES, "confirmation_request_json"):
            batch.drop_column(name)
    with op.batch_alter_table("memory_candidates", recreate="always") as batch:
        batch.drop_index("uq_memory_candidates_request")
        batch.drop_index("ix_memory_candidates_source")
        for suffix in ("source", "source_kind", "source_verification"):
            batch.drop_constraint(
                op.f(f"ck_memory_candidates_ck_memory_candidates_{suffix}"), type_="check"
            )
        for name in (
            *_SOURCE_NAMES,
            "idempotency_key",
            "request_fingerprint",
            "confirmation_fingerprint",
        ):
            batch.drop_column(name)
        batch.create_check_constraint(
            op.f("ck_memory_candidates_ck_memory_candidates_source"),
            "source_message_id IS NOT NULL OR source_document_ref IS NOT NULL",
        )
