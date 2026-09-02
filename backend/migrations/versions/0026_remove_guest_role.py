"""Remove the retired guest space role from the membership constraint."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_remove_guest_role"
down_revision: str | None = "0025_personal_family_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild_space_members(role_check: str) -> None:
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        sa.text(
            "CREATE TABLE space_members_new ("
            "id INTEGER PRIMARY KEY, space_id INTEGER NOT NULL "
            "REFERENCES family_spaces(id) ON DELETE CASCADE,"
            "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            "added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            "role VARCHAR(16) NOT NULL DEFAULT 'member' "
            f"CHECK({role_check}),"
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


def upgrade() -> None:
    conn = op.get_bind()
    guest_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM space_members WHERE role = 'guest'")
    ).scalar_one()
    if guest_count:
        raise RuntimeError(
            "0026_remove_guest_role aborted: space_members contains "
            f"{guest_count} guest row(s); migrate data and re-review before upgrading"
        )
    _rebuild_space_members("role IN ('space_admin','member')")


def downgrade() -> None:
    op.drop_index("uq_space_active_admin", table_name="space_members")
    op.drop_index("ix_space_members_user", table_name="space_members")
    op.drop_index("ix_space_members_space", table_name="space_members")
    _rebuild_space_members("role IN ('space_admin','member','guest')")
