"""m1d 画布位置记忆：node_positions（UNIQUE(space,user)，双 FK CASCADE）。

Revision ID: 0006_m1d_positions
Revises: 0005_m1c_spaces
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_m1d_positions"
down_revision: str | None = "0005_m1c_spaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_positions",
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
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.UniqueConstraint("space_id", "user_id", name="uq_node_position_pair"),
    )


def downgrade() -> None:
    op.drop_table("node_positions")
