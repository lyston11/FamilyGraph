"""m1c 家庭空间与成员资格：family_spaces / space_members（AD-3/AD-4/§5）。

Revision ID: 0005_m1c_spaces
Revises: 0004_m1b_relations
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_m1c_spaces"
down_revision: str | None = "0004_m1b_relations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "family_spaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "space_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "added_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "role",
            sa.String(16),
            sa.CheckConstraint("role IN ('owner','member')", name="ck_sm_role"),
            server_default="member",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('pending','active','rejected','withdrawn','removed')",
                name="ck_sm_status",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("space_id", "user_id", name="uq_space_member_pair"),
    )
    op.create_index("ix_space_members_space", "space_members", ["space_id"])
    op.create_index("ix_space_members_user", "space_members", ["user_id"])

    # 合并请求（AD-4）：relation 携带可选空间成员意图，accept 时同事务激活
    # SQLite 不支持 ALTER 加约束 → batch 模式重建表
    with op.batch_alter_table("relations") as batch:
        batch.add_column(
            sa.Column(
                "pending_space_id",
                sa.Integer(),
                sa.ForeignKey("family_spaces.id", ondelete="SET NULL"),
                nullable=True,
            )
        )


def downgrade() -> None:
    op.drop_column("relations", "pending_space_id")
    op.drop_index("ix_space_members_user", table_name="space_members")
    op.drop_index("ix_space_members_space", table_name="space_members")
    op.drop_table("space_members")
    op.drop_table("family_spaces")
