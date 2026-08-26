"""term_registry：TermEntry 四级称谓 / TermUsage 使用证据（V2.3 Block E3）。

变更总览（task 08-26-v2-3-relationship-intelligence design.md「Term 选择与学习」节）：
- term_entries：四级称谓词条（system/locale/space/personal）。personal 以
  partial unique 保证每账号每概念至多一条 active（旧值 superseded 保留
  revision 链）；space 层同 (space_id, concept_code) 允许多条 active 别名、
  同 term 唯一。晋升由两人 usage 规则自动产生（无管理员发布），降级仅置
  superseded，不复制到 locale/system。
- term_usages：称谓使用证据。UNIQUE(term_entry_id, account_id, space_id)
  ——同账号重复选择不计第二位使用者（KI-4）。两位不同 identity_confirmed
  账号的有效 usage 是空间词自动晋升的唯一依据。
- 种子数据：system 标准称谓兜底集；locale zh-CN 覆盖 E2 黄金用例 code 集；
  wu 方言示例条目一条。清单与 services/terms.seed_builtin_packs 共用
  （models/term_registry.BUILTIN_TERM_SEEDS），可扩展注册表，首版不做
  全量地方叫法。

说明：SQLite 迁移按非事务 DDL 处理；downgrade 仅结构还原（种子随表删除）。

Revision ID: 0012_term_registry
Revises: 0011_derived_facts
Create Date: 2026-08-26
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from app.models.term_registry import BUILTIN_TERM_SEEDS

revision: str = "0012_term_registry"
down_revision: str | None = "0011_derived_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_TIME = datetime(2026, 8, 26)

_entries_table = sa.table(
    "term_entries",
    sa.column("concept_code", sa.String),
    sa.column("level", sa.String),
    sa.column("space_id", sa.Integer),
    sa.column("owner_account_id", sa.Integer),
    sa.column("locale", sa.String),
    sa.column("term", sa.String),
    sa.column("status", sa.String),
    sa.column("revision", sa.Integer),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def upgrade() -> None:
    op.create_table(
        "term_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("concept_code", sa.String(128), nullable=False),
        sa.Column(
            "level",
            sa.String(16),
            sa.CheckConstraint(
                "level IN ('system','locale','space','personal')", name="ck_te_level"
            ),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "owner_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("locale", sa.String(16), nullable=True),
        sa.Column("term", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint("status IN ('active','superseded')", name="ck_te_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_term_entries_personal_active",
        "term_entries",
        ["owner_account_id", "concept_code"],
        unique=True,
        sqlite_where=sa.text("level = 'personal' AND status = 'active'"),
    )
    op.create_index(
        "uq_term_entries_space_active",
        "term_entries",
        ["space_id", "concept_code", "term"],
        unique=True,
        sqlite_where=sa.text("level = 'space' AND status = 'active'"),
    )
    op.create_index("ix_term_entries_space_level", "term_entries", ["space_id", "level"])
    op.create_index("ix_term_entries_concept", "term_entries", ["concept_code"])
    op.create_index("ix_term_entries_owner", "term_entries", ["owner_account_id"])

    op.create_table(
        "term_usages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "term_entry_id",
            sa.Integer(),
            sa.ForeignKey("term_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_event",
            sa.String(24),
            sa.CheckConstraint(
                "source_event IN ('personal_correction','assistant_query','manual_select')",
                name="ck_tu_source_event",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_term_usages_entry_account_space",
        "term_usages",
        ["term_entry_id", "account_id", "space_id"],
        unique=True,
    )
    op.create_index("ix_term_usages_account", "term_usages", ["account_id"])
    op.create_index("ix_term_usages_space", "term_usages", ["space_id"])

    # 内置包种子（幂等语义由 downgrade 随表删除保证；测试内重灌走
    # services/terms.seed_builtin_packs，同一份清单）
    op.bulk_insert(
        _entries_table,
        [
            {
                "concept_code": code,
                "level": level,
                "space_id": None,
                "owner_account_id": None,
                "locale": locale,
                "term": term,
                "status": "active",
                "revision": 1,
                "created_at": _SEED_TIME,
                "updated_at": _SEED_TIME,
            }
            for level, locale, code, term in BUILTIN_TERM_SEEDS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_term_usages_space", table_name="term_usages")
    op.drop_index("ix_term_usages_account", table_name="term_usages")
    op.drop_index("uq_term_usages_entry_account_space", table_name="term_usages")
    op.drop_table("term_usages")
    op.drop_index("ix_term_entries_owner", table_name="term_entries")
    op.drop_index("ix_term_entries_concept", table_name="term_entries")
    op.drop_index("ix_term_entries_space_level", table_name="term_entries")
    op.drop_index("uq_term_entries_space_active", table_name="term_entries")
    op.drop_index("uq_term_entries_personal_active", table_name="term_entries")
    op.drop_table("term_entries")
