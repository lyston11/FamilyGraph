"""系统管理员主体、空间唯一管理员与交接同意工单。

迁移原则：旧 platform_operator 账号被提升为独立 system_admin 主体，旧 User
仍作为普通家庭档案保留但不再持有平台角色；空间的 owner/space_admin 候选必须
恰好一个，否则以可诊断错误中止，不随机修复。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_system_admin_space_manager"
down_revision: str | None = "0021_space_manager_application"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(conn: sa.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": name},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
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

    # Promote legacy platform operators without copying any family relation into the
    # platform subject. Existing User rows remain ordinary family users.
    if _table_exists(conn, "platform_role_assignments"):
        rows = conn.execute(
            sa.text(
                "SELECT pra.account_id, a.user_id, u.name, u.created_at, a.pin_hash, "
                "a.token_version FROM platform_role_assignments pra "
                "JOIN accounts a ON a.id=pra.account_id JOIN users u ON u.id=a.user_id "
                "WHERE pra.role='platform_operator' ORDER BY pra.id"
            )
        ).mappings()
        for row in rows:
            conn.execute(
                sa.text(
                    "INSERT INTO system_admins (login_name,status,created_at) "
                    "VALUES (:name,'active',:created_at)"
                ),
                {"name": row["name"], "created_at": row["created_at"]},
            )
            system_admin_id = conn.execute(sa.text("SELECT last_insert_rowid()")).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO system_admin_accounts "
                    "(system_admin_id,pin_hash,pin_must_change,token_version,"
                    "failed_attempts,status) "
                    "VALUES (:id,:pin,1,:version,0,'managed')"
                ),
                {
                    "id": system_admin_id,
                    "pin": row["pin_hash"],
                    "version": int(row["token_version"]) + 1,
                },
            )
        conn.execute(sa.text("DELETE FROM platform_role_assignments"))

    # Normalize owner compatibility rows to the single canonical role. Do not guess
    # when owner_id and active role candidates disagree.
    spaces = conn.execute(sa.text("SELECT id, owner_id FROM family_spaces ORDER BY id")).mappings()
    for space in spaces:
        members = (
            conn.execute(
                sa.text(
                    "SELECT id,user_id,role FROM space_members "
                    "WHERE space_id=:space_id AND status='active'"
                ),
                {"space_id": space["id"]},
            )
            .mappings()
            .all()
        )
        candidates = {
            (int(m["user_id"]), str(m["role"]))
            for m in members
            if m["role"] in ("owner", "space_admin")
        }
        owner_member = next((m for m in members if m["user_id"] == space["owner_id"]), None)
        if owner_member is not None:
            candidates.add((int(owner_member["user_id"]), "owner_id"))
        users = {user_id for user_id, _role in candidates}
        if len(users) != 1:
            detail = sorted(candidates)
            raise RuntimeError(
                f"space manager migration conflict: space_id={space['id']} candidates={detail}"
            )
        manager_id = next(iter(users))
        manager_row = next(m for m in members if int(m["user_id"]) == manager_id)
        conn.execute(
            sa.text("UPDATE space_members SET role='space_admin' WHERE id=:id"),
            {"id": manager_row["id"]},
        )

    # Replace the old role CHECK so owner cannot re-enter as a second product role.
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        sa.text(
            "CREATE TABLE space_members_new ("
            "id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL "
            "REFERENCES family_spaces(id) ON DELETE CASCADE,"
            "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            "added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            "role VARCHAR(16) NOT NULL DEFAULT 'member' "
            "CHECK(role IN ('space_admin','member','guest')),"
            "status VARCHAR(16) NOT NULL DEFAULT 'pending' "
            "CHECK(status IN ('pending','active','rejected','withdrawn','removed')),"
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,"
            "CONSTRAINT uq_space_member_pair UNIQUE(space_id,user_id))"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO space_members_new "
            "SELECT id,space_id,user_id,added_by,role,status,created_at,updated_at "
            "FROM space_members"
        )
    )
    conn.execute(sa.text("DROP TABLE space_members"))
    conn.execute(sa.text("ALTER TABLE space_members_new RENAME TO space_members"))
    conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    op.create_index("ix_space_members_space", "space_members", ["space_id"])
    op.create_index("ix_space_members_user", "space_members", ["user_id"])
    op.create_index(
        "uq_space_active_admin",
        "space_members",
        ["space_id"],
        unique=True,
        sqlite_where=sa.text("role='space_admin' AND status='active'"),
    )

    op.create_table(
        "manager_transfer_consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("space_manager_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "current_manager_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("response_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','expired')", name="ck_mtc_status"
        ),
        sa.UniqueConstraint("application_id", name="uq_mtc_application"),
    )
    op.create_index(
        "ix_manager_transfer_consents_manager",
        "manager_transfer_consents",
        ["current_manager_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_manager_transfer_consents_manager", table_name="manager_transfer_consents")
    op.drop_table("manager_transfer_consents")
    op.drop_index("uq_space_active_admin", table_name="space_members")
    op.drop_index("ix_space_members_user", table_name="space_members")
    op.drop_index("ix_space_members_space", table_name="space_members")
    # A downgrade intentionally does not recreate owner roles or legacy platform users.
    op.drop_index(
        "ix_system_admin_refresh_sessions_system_admin_id",
        table_name="system_admin_refresh_sessions",
    )
    op.drop_table("system_admin_refresh_sessions")
    op.drop_table("system_admin_accounts")
    op.drop_index("ix_system_admins_login_name", table_name="system_admins")
    op.drop_table("system_admins")
