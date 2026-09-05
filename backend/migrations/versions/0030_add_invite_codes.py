"""邀请码表（09-05 家庭账号开通与注册流程，纯增量新表）。

CHECK 约束（design.md §2 合同）：
- kind='stranger' ⇔ space_id IS NULL（陌生人码不绑定空间）
- kind IN ('household','lineage') ⇒ space_id NOT NULL 且 max_uses=1（一次性，用后即焚）
- stranger 码 max_uses 可为 NULL（不限次）或 >= 1（创建时可选使用上限）
- used_count 非负且不超过 max_uses（NULL=不限）
码字符集去 0/O/1/I/L，生成原语在 services/invite_codes.py；回滚 = 删表，不动既有列。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_add_invite_codes"
down_revision: str | None = "0029_admin_access_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invite_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("space_id", sa.Integer(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('household','lineage','stranger')", name="ck_invite_code_kind"
        ),
        sa.CheckConstraint(
            "(kind = 'stranger' AND space_id IS NULL) "
            "OR (kind IN ('household','lineage') AND space_id IS NOT NULL)",
            name="ck_invite_code_space_pair",
        ),
        sa.CheckConstraint(
            # 注意 SQLite CHECK 语义：表达式为 NULL 视为通过， household/lineage 的
            # max_uses=1 必须显式排除 NULL（max_uses IS NOT NULL），否则 NULL=1 → NULL
            # 会被静默放行。
            "(kind IN ('household','lineage') AND max_uses IS NOT NULL AND max_uses = 1) "
            "OR (kind = 'stranger' AND (max_uses IS NULL OR max_uses >= 1))",
            name="ck_invite_code_max_uses",
        ),
        sa.CheckConstraint(
            "used_count >= 0 AND (max_uses IS NULL OR used_count <= max_uses)",
            name="ck_invite_code_used_count",
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["space_id"], ["family_spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_invite_codes_code"),
    )
    op.create_index("ix_invite_codes_creator", "invite_codes", ["creator_id"])
    op.create_index("ix_invite_codes_space", "invite_codes", ["space_id"])


def downgrade() -> None:
    op.drop_index("ix_invite_codes_space", table_name="invite_codes")
    op.drop_index("ix_invite_codes_creator", table_name="invite_codes")
    op.drop_table("invite_codes")
