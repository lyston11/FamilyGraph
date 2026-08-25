"""m1a 档案与代管权：users 增量列（纯增量，downgrade 删列回滚）。

契约来源：m1a design.md 数据契约 + spec/architecture.md §1 [AD-1]、§7 [AD-5]。
- gender/birth/death/bio/avatar_path：档案字段（avatar 上传 m3a 启用，本任务仅列）
- privacy_mode：D5 归属模式（perpetual|handover）
- created_by：代管创建者自引用 FK；档案删除后审计仍保留创建者语义 → SET NULL
  （SQLite 原生支持 ADD COLUMN 带 REFERENCES 且默认 NULL，Alembic SQLite 方言
  不渲染 ALTER 约束，此处以等价原生 DDL 执行，保持纯增量、免整表重建）
- claim_status：ClaimState（managed|claimed），managed→claimed 唯一转换点在首登改 PIN
- deleted_at：v1 硬删除，列为审计查询预留，不启用软删路径
- clan_disclosure_json：AD-9 家族空间外披露开关，默认全 false

Revision ID: 0003_m1a_profiles
Revises: 0002_m0b_auth
Create Date: 2026-08-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_m1a_profiles"
down_revision: str | None = "0002_m0b_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISCLOSURE_DEFAULT = (
    '{"avatar": false, "photos": false, ' '"dates": false, "bio": false, "attachments": false}'
)


def upgrade() -> None:
    # 行内 CHECK 随列定义添加/删除（SQLite ADD COLUMN 原生支持）；
    # 约束名经 naming_convention 渲染为 ck_users_<name>
    op.add_column(
        "users",
        sa.Column(
            "gender",
            sa.String(length=9),
            sa.CheckConstraint("gender IN ('m', 'f', 'unknown')", name="gender"),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column("users", sa.Column("birth", sa.JSON(), nullable=True))
    op.add_column("users", sa.Column("death", sa.JSON(), nullable=True))
    op.add_column("users", sa.Column("bio", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("avatar_path", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "privacy_mode",
            sa.String(length=16),
            sa.CheckConstraint("privacy_mode IN ('perpetual', 'handover')", name="privacy_mode"),
            nullable=False,
            server_default="handover",
        ),
    )
    # 自引用 FK：SQLite 要求 ADD COLUMN 带 REFERENCES 时默认值为 NULL（满足）
    op.execute(
        "ALTER TABLE users ADD COLUMN created_by INTEGER"
        " REFERENCES users (id) ON DELETE SET NULL"
    )
    op.add_column(
        "users",
        sa.Column(
            "claim_status",
            sa.String(length=16),
            sa.CheckConstraint("claim_status IN ('managed', 'claimed')", name="claim_status"),
            nullable=False,
            server_default="managed",
        ),
    )
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "clan_disclosure_json",
            sa.JSON(),
            nullable=False,
            server_default=_DISCLOSURE_DEFAULT,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "clan_disclosure_json")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "claim_status")
    op.drop_column("users", "created_by")
    op.drop_column("users", "privacy_mode")
    op.drop_column("users", "avatar_path")
    op.drop_column("users", "bio")
    op.drop_column("users", "death")
    op.drop_column("users", "birth")
    op.drop_column("users", "gender")
