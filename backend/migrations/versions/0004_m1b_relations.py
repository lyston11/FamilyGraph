"""m1b 关系模型：relations 表（architecture.md §4 FSM / §5 DB 契约）。

- 方向语义：to_user 是 from_user 的 dir_class（创建者视角 [D2]）
- 自环禁令 CHECK；双向 partial unique index 保证每对用户仅一条非终态边
  （SQLite partial index 落地为 (from,to) 与 (to,from) 两条，均 WHERE 非终态）
- 终态不可复活；重连 = 新边（唯一约束只覆盖 pending/active）

Revision ID: 0004_m1b_relations
Revises: 0003_m1a_profiles
Create Date: 2026-08-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_m1b_relations"
down_revision: str | None = "0003_m1a_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "from_user",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_user",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dir_class",
            sa.String(16),
            sa.CheckConstraint(
                "dir_class IN ('elder','younger','peer','spouse')",
                name="ck_relations_dir_class",
            ),
            nullable=False,
        ),
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('pending','active','rejected','cancelled','revoked')",
                name="ck_relations_status",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("from_user != to_user", name="ck_relations_no_self_loop"),
    )
    op.create_index("ix_relations_from", "relations", ["from_user"])
    op.create_index("ix_relations_to", "relations", ["to_user"])
    # 每对用户仅一条非终态边：两个方向各一条 partial unique index
    op.create_index(
        "uq_relations_pair_fwd",
        "relations",
        ["from_user", "to_user"],
        unique=True,
        sqlite_where=sa.text("status IN ('pending','active')"),
    )
    op.create_index(
        "uq_relations_pair_rev",
        "relations",
        ["to_user", "from_user"],
        unique=True,
        sqlite_where=sa.text("status IN ('pending','active')"),
    )


def downgrade() -> None:
    op.drop_index("uq_relations_pair_rev", table_name="relations")
    op.drop_index("uq_relations_pair_fwd", table_name="relations")
    op.drop_index("ix_relations_to", table_name="relations")
    op.drop_index("ix_relations_from", table_name="relations")
    op.drop_table("relations")
