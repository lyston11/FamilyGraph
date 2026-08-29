"""atomic_member_creation：建档幂等台账（F1/R-01 原子建档）。

为「名字+关系」单一原子命令提供重放保护：同 (actor, idempotency_key) 至多
一行，记录请求内容 hash 与已创建档案/关系，使并发/重试不产生重复档案或边。
本轮无真实数据，只新增结构，不回填存量。

Revision ID: 0016_atomic_member_creation
Revises: 0015_controlled_web
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_atomic_member_creation"
down_revision: str | None = "0015_controlled_web"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_creation_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "member_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "relation_id",
            sa.Integer(),
            sa.ForeignKey("relations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("actor_user_id", "idempotency_key", name="uq_mcr_actor_key"),
    )
    op.create_index(
        "ix_member_creation_requests_member", "member_creation_requests", ["member_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_member_creation_requests_member", table_name="member_creation_requests")
    op.drop_table("member_creation_requests")
