"""account_bindings 表（09-05 并流绑定，决策 16；纯增量新表，独立回滚单元）。

建档/邀请查重命中已存在的自注册账号时，不建第二份可登录凭据，改创建 pending
绑定请求（commands/members.py 撞名分支 + commands/bindings.py 确认流）。
回滚 = 删表，不动既有列；person_id SET NULL 保证人物并回后绑定记录仍可保留。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_add_account_bindings"
down_revision: str | None = "0030_add_invite_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("initiator_id", sa.Integer(), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','rejected','cancelled')",
            name="ck_account_binding_status",
        ),
        sa.ForeignKeyConstraint(["initiator_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_bindings_target", "account_bindings", ["target_id"])
    op.create_index("ix_account_bindings_initiator", "account_bindings", ["initiator_id"])
    op.create_index("ix_account_bindings_person", "account_bindings", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_account_bindings_person", table_name="account_bindings")
    op.drop_index("ix_account_bindings_initiator", table_name="account_bindings")
    op.drop_index("ix_account_bindings_target", table_name="account_bindings")
    op.drop_table("account_bindings")
