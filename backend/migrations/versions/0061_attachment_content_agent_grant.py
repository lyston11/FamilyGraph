"""附件内容版本与 Agent 使用授权真源（10-11）。

新表默认为空，不回填历史附件；只有用户显式确认内容并创建 grant 才会产生行。
降级若存在任何内容版本或授权证据则拒绝，避免把已确认的用户资料与授权静默丢失。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations._preflight import run_ancestor_preflight

revision = "0061_attachment_content_agent_grant"
down_revision = "0060_steward_memory_scopes"
branch_labels = None
depends_on = None

CONTENT_TABLE = "attachment_content_versions"
GRANT_TABLE = "agent_content_grants"


def _table_exists(name: str) -> bool:
    from sqlalchemy import inspect

    return name in inspect(op.get_bind()).get_table_names()


def _count(table: str) -> int:
    return int(op.get_bind().scalar(sa.text(f"SELECT COUNT(*) FROM {table}")) or 0)


def upgrade() -> None:
    if not _table_exists(CONTENT_TABLE):
        op.create_table(
            CONTENT_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("attachment_id", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("content_kind", sa.String(length=32), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("extractor_name", sa.String(length=64), nullable=False),
            sa.Column("extractor_version", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("confirmed_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "content_kind IN ("
                "'manual_text','ocr_text','link_metadata','web_snapshot','document_text'"
                ")",
                name="ck_acv_content_kind",
            ),
            sa.CheckConstraint(
                "status IN ('draft','confirmed','rejected','superseded','revoked')",
                name="ck_acv_status",
            ),
            sa.CheckConstraint("revision >= 1", name="ck_acv_revision_positive"),
            sa.CheckConstraint("length(text) > 0", name="ck_acv_text_nonempty"),
            sa.CheckConstraint("length(content_sha256) = 64", name="ck_acv_hash_length"),
            sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("attachment_id", "revision", name="uq_acv_attachment_revision"),
        )
        op.create_index("ix_acv_attachment_status", CONTENT_TABLE, ["attachment_id", "status"])
        op.create_index("ix_acv_status", CONTENT_TABLE, ["status"])

    if not _table_exists(GRANT_TABLE):
        op.create_table(
            GRANT_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("content_version_id", sa.Integer(), nullable=False),
            sa.Column("space_id", sa.Integer(), nullable=False),
            sa.Column("agent_kind", sa.String(length=16), nullable=False),
            sa.Column("purpose", sa.String(length=16), nullable=False),
            sa.Column("grant_scope", sa.String(length=16), nullable=False),
            sa.Column("grantor_user_id", sa.Integer(), nullable=True),
            sa.Column("grantor_account_id", sa.Integer(), nullable=True),
            sa.Column("authorization_revision", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoke_reason", sa.String(length=64), nullable=True),
            sa.CheckConstraint("agent_kind IN ('assistant')", name="ck_acg_agent_kind"),
            sa.CheckConstraint("purpose IN ('rag')", name="ck_acg_purpose"),
            sa.CheckConstraint("grant_scope IN ('whole')", name="ck_acg_scope"),
            sa.CheckConstraint("status IN ('active','revoked','expired')", name="ck_acg_status"),
            sa.CheckConstraint("authorization_revision >= 1", name="ck_acg_revision_positive"),
            sa.ForeignKeyConstraint(
                ["content_version_id"], [f"{CONTENT_TABLE}.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["space_id"], ["family_spaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["grantor_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["grantor_account_id"], ["accounts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_acg_content_version", GRANT_TABLE, ["content_version_id"])
        op.create_index("ix_acg_space_status", GRANT_TABLE, ["space_id", "status"])
        op.create_index("ix_acg_status", GRANT_TABLE, ["status"])
        op.create_index(
            "uq_acg_active_grant",
            GRANT_TABLE,
            ["content_version_id", "space_id", "agent_kind", "purpose", "grant_scope"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )


def downgrade() -> None:
    # Ancestor guards must run before any DDL; SQLite DDL is not reliably rollback-safe.
    run_ancestor_preflight(op.get_bind(), op.get_context(), down_revision=down_revision)
    if _table_exists(GRANT_TABLE) and _count(GRANT_TABLE):
        raise RuntimeError(
            "refusal: agent content grant evidence exists; retain data and roll forward"
        )
    if _table_exists(CONTENT_TABLE) and _count(CONTENT_TABLE):
        raise RuntimeError(
            "refusal: attachment content version evidence exists; retain data and roll forward"
        )
    if _table_exists(GRANT_TABLE):
        op.drop_table(GRANT_TABLE)
    if _table_exists(CONTENT_TABLE):
        op.drop_table(CONTENT_TABLE)
