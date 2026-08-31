"""申请记录的系统管理员裁决人引用。

系统管理员是独立主体，不是家庭 ``users`` 行，因此 ``decided_by`` 无法表达其
裁决身份。新增独立列而不是放宽既有外键，避免把两类主体挤进同一个引用。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_system_admin_decision_ref"
down_revision: str | None = "0022_system_admin_space_manager"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(conn: sa.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(sa.text(f"PRAGMA table_info('{table}')")).fetchall()}


def upgrade() -> None:
    conn = op.get_bind()
    if "system_admin_decided_by" in _columns(conn, "space_manager_applications"):
        return
    with op.batch_alter_table("space_manager_applications") as batch:
        batch.add_column(sa.Column("system_admin_decided_by", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_sma_system_admin_decided_by",
            "system_admins",
            ["system_admin_decided_by"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    conn = op.get_bind()
    if "system_admin_decided_by" not in _columns(conn, "space_manager_applications"):
        return
    with op.batch_alter_table("space_manager_applications") as batch:
        batch.drop_constraint("fk_sma_system_admin_decided_by", type_="foreignkey")
        batch.drop_column("system_admin_decided_by")
