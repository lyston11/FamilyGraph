"""invite_codes.creator_id 改为 SET NULL（09-05 P2-2 删除预检修复，纯约束变更）。

0030 将 creator_id 定为 NOT NULL + RESTRICT，意图是「创建者删除不掩盖撤销历史」，
但 RESTRICT 对已核销/已撤销码同样生效（子行仍在），而产品只提供撤销不提供删码——
任何创建过码的用户都永远无法删除/注销，删除命令兜底把它伪装成
OWNER_TRANSFER_REQUIRED 409（验收 P2-2 缺陷）。

修复分两层：
- 约束：creator_id 放宽为 nullable + ON DELETE SET NULL。码行（含使用计数/撤销
  状态）在创建者删除后保留，仅清除人物指针——与 account_bindings.person_id SET
  NULL 同一「保记录、清指针」哲学；归因历史由 audit_log（invite_code_created/
  redeemed/revoked，无 FK 快照）继续承载。
- 命令：delete_profile_core 删除前自动撤销该创建者所有未撤销码并写 audit
  （invite_code_auto_revoked_on_delete），防止遗留仍可兑换的分享凭据。

SQLite 不能 ALTER FK，按 0026/0028 同款重建：建新表 → 拷贝 → 删旧 → 改名 →
重建索引；不在迁移内切换 PRAGMA foreign_keys。downgrade 发现 creator_id 为 NULL
的行（创建者已删除）fail-closed 中止，禁止静默丢弃归属历史。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_invite_codes_creator_set_null"
down_revision: str | None = "0031_add_account_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INVITE_CODE_COLUMNS = (
    "id, code, kind, creator_id, space_id, max_uses, used_count, expires_at, "
    "revoked_at, created_at"
)


def _create_invite_codes(*, creator_nullable: bool) -> None:
    """按目标形态建表（仅 creator_id 的 nullable 与 FK 动作随方向不同）。"""
    op.create_table(
        "invite_codes_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=creator_nullable),
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
            # SQLite CHECK 语义：表达式为 NULL 视为通过，household/lineage 的
            # max_uses=1 必须显式排除 NULL（与 0030 逐字一致）。
            "(kind IN ('household','lineage') AND max_uses IS NOT NULL AND max_uses = 1) "
            "OR (kind = 'stranger' AND (max_uses IS NULL OR max_uses >= 1))",
            name="ck_invite_code_max_uses",
        ),
        sa.CheckConstraint(
            "used_count >= 0 AND (max_uses IS NULL OR used_count <= max_uses)",
            name="ck_invite_code_used_count",
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"],
            ["users.id"],
            ondelete="SET NULL" if creator_nullable else "RESTRICT",
        ),
        sa.ForeignKeyConstraint(["space_id"], ["family_spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_invite_codes_code"),
    )


def _finalize_invite_codes() -> None:
    """删旧表 → 改名 → 重建索引（约束名与 0030 完全对齐）。"""
    op.drop_table("invite_codes")
    op.rename_table("invite_codes_new", "invite_codes")
    op.create_index("ix_invite_codes_creator", "invite_codes", ["creator_id"])
    op.create_index("ix_invite_codes_space", "invite_codes", ["space_id"])


def upgrade() -> None:
    _create_invite_codes(creator_nullable=True)
    op.execute(
        f"INSERT INTO invite_codes_new ({_INVITE_CODE_COLUMNS}) "
        f"SELECT {_INVITE_CODE_COLUMNS} FROM invite_codes"
    )
    _finalize_invite_codes()


def downgrade() -> None:
    bind = op.get_bind()
    orphan_count = bind.scalar(
        sa.text("SELECT COUNT(*) FROM invite_codes WHERE creator_id IS NULL")
    )
    if orphan_count:
        raise RuntimeError(
            "invite_codes downgrade requires an explicit data decision: "
            f"{orphan_count} row(s) have no creator (deleted profile); "
            "restoring NOT NULL would silently drop attribution history"
        )
    _create_invite_codes(creator_nullable=False)
    op.execute(
        f"INSERT INTO invite_codes_new ({_INVITE_CODE_COLUMNS}) "
        f"SELECT {_INVITE_CODE_COLUMNS} FROM invite_codes"
    )
    _finalize_invite_codes()
