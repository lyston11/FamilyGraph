"""m3a 附件：attachments（image/link/location 枚举占位；双 FK CASCADE）。

Revision ID: 0007_m3a_attachments
Revises: 0006_m1d_positions
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_m3a_attachments"
down_revision: str | None = "0006_m1d_positions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "type",
            sa.String(16),
            sa.CheckConstraint("type IN ('image','link','location')", name="ck_att_type"),
            nullable=False,
        ),
        sa.Column("url_or_path", sa.String(500), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_attachments_user", "attachments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_user", table_name="attachments")
    op.drop_table("attachments")
