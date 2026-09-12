"""系统管理员访问会话与独立审计表（09-04 子任务 2）。

两表永久保留：审计行的 system_admin_id/session_id 外键均为 SET NULL，
业务主体删除不级联删除审计历史。会话只存 token_hash（SHA-256 hex）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_admin_access_audit"
down_revision: str | None = "0028_admin_password_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_access_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("system_admin_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "target_type IN ('user','space')", name="ck_admin_access_session_target"
        ),
        sa.ForeignKeyConstraint(["system_admin_id"], ["system_admins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_admin_access_sessions_token_hash"),
    )
    op.create_index(
        "ix_admin_access_sessions_admin",
        "admin_access_sessions",
        ["system_admin_id"],
    )
    op.create_table(
        "admin_access_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("system_admin_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "target_type IS NULL OR target_type IN ('user','space')",
            name="ck_admin_access_audit_target",
        ),
        sa.ForeignKeyConstraint(["system_admin_id"], ["system_admins.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["admin_access_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_access_audits_created_at", "admin_access_audits", ["created_at"])
    op.create_index("ix_admin_access_audits_admin", "admin_access_audits", ["system_admin_id"])
    op.create_index(
        "ix_admin_access_audits_target", "admin_access_audits", ["target_type", "target_id"]
    )
    op.create_index("ix_admin_access_audits_session", "admin_access_audits", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_access_audits_session", table_name="admin_access_audits")
    op.drop_index("ix_admin_access_audits_target", table_name="admin_access_audits")
    op.drop_index("ix_admin_access_audits_admin", table_name="admin_access_audits")
    op.drop_index("ix_admin_access_audits_created_at", table_name="admin_access_audits")
    op.drop_table("admin_access_audits")
    op.drop_index("ix_admin_access_sessions_admin", table_name="admin_access_sessions")
    op.drop_table("admin_access_sessions")
