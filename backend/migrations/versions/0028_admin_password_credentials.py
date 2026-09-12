"""系统管理员凭据改型：PIN → 用户名 + 强密码（09-04 SF-F2）。

合同（任务 09-04-system-admin-auth-api-isolation design §3）：
- system_admins: login_name → username（唯一），新增 updated_at；
- system_admin_accounts: pin_hash/pin_must_change/token_version →
  password_hash/password_must_change/password_version，新增 created_at/updated_at；
- system_admin_refresh_sessions: 新增 created_at/last_seen_at（轮换链审计）。

不做旧 PIN 账号迁移，也不做兼容登录窗口：upgrade 前检测到任何旧主体/凭据行
即以 RuntimeError 中止（fail-closed），原 schema 与数据保持不变。旧 PIN 结构
若未经本迁移仍留在库中，由启动 preflight 拒绝服务（services/admin_bootstrap）。

SQLite 重建遵循 database-guidelines：不在迁移内切换连接级 PRAGMA foreign_keys，
FK 删除动作以显式列定义保留；重建仅重命名/重排列，不转换任何数据。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_admin_password_credentials"
down_revision: str | None = "0027_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES_ABORT = ("system_admins", "system_admin_accounts")


def _table_exists(conn: sa.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": name},
        ).scalar()
    )


def _abort_on_legacy_rows(conn: sa.Connection) -> None:
    """旧凭据行存在时中止：本迁移不把 PIN 数据转换为密码（设计裁定 fail-closed）。

    原 schema 与数据保持不变，由运维显式处置旧主体后再升级；启动 preflight
    （services/admin_bootstrap）会对未经本迁移的旧 PIN 结构拒绝服务。
    """
    for table in _TABLES_ABORT:
        if not _table_exists(conn, table):
            continue
        count = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"0028_admin_password_credentials aborted: {table} contains {count} row(s) "
                "of legacy credentials; this migration never converts accounts in place. "
                "Resolve the legacy system admin accounts explicitly before switching schema."
            )


def _drop_legacy() -> None:
    op.drop_index(
        "ix_system_admin_refresh_sessions_system_admin_id",
        table_name="system_admin_refresh_sessions",
    )
    op.drop_table("system_admin_refresh_sessions")
    op.drop_table("system_admin_accounts")
    op.drop_index("ix_system_admins_login_name", table_name="system_admins")
    op.drop_table("system_admins")


def _create_password_schema() -> None:
    op.create_table(
        "system_admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_system_admin_status"),
    )
    op.create_index("ix_system_admins_username", "system_admins", ["username"], unique=True)
    op.create_table(
        "system_admin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "system_admin_id",
            sa.Integer(),
            sa.ForeignKey("system_admins.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "password_must_change", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("password_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="managed"),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('managed','claimed')", name="ck_system_admin_account_status"
        ),
    )
    op.create_table(
        "system_admin_refresh_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "system_admin_id",
            sa.Integer(),
            sa.ForeignKey("system_admins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "rotated_from",
            sa.Integer(),
            sa.ForeignKey("system_admin_refresh_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_system_admin_refresh_sessions_admin",
        "system_admin_refresh_sessions",
        ["system_admin_id"],
    )


def _create_pin_schema() -> None:
    """恢复 0022 的旧 PIN 结构（仅结构回滚能力，不恢复任何历史凭据行）。"""
    op.create_table(
        "system_admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("login_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_system_admin_status"),
    )
    op.create_index("ix_system_admins_login_name", "system_admins", ["login_name"])
    op.create_table(
        "system_admin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "system_admin_id",
            sa.Integer(),
            sa.ForeignKey("system_admins.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("pin_hash", sa.String(255), nullable=False),
        sa.Column("pin_must_change", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="managed"),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('managed','claimed')", name="ck_system_admin_accounts_status"
        ),
    )
    op.create_table(
        "system_admin_refresh_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "system_admin_id",
            sa.Integer(),
            sa.ForeignKey("system_admins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "rotated_from",
            sa.Integer(),
            sa.ForeignKey("system_admin_refresh_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_system_admin_refresh_sessions_system_admin_id",
        "system_admin_refresh_sessions",
        ["system_admin_id"],
    )


def upgrade() -> None:
    conn = op.get_bind()
    _abort_on_legacy_rows(conn)
    _drop_legacy()
    _create_password_schema()


def downgrade() -> None:
    conn = op.get_bind()
    _abort_on_legacy_rows(conn)
    _drop_legacy()
    _create_pin_schema()
