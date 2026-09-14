"""steward_inferred_edges：管家推测层（任务 09-13-steward-inferred-tree-layer）。

变更总览：
- ``steward_inferred_edges``：管家推断的空间级单跳原子关系建议（推测边）。
  部分唯一索引 uq_sie_active_triple 保证每 (space, subject, object, kind)
  三元组至多一条 proposed 行；evidence_hash 为投影/冷却/失效判定键；
  ``origin`` 白名单 llm/rule/intake（v1 只产 llm）。
- ``agent_space_provider_settings`` 新增 ``inferred_tree`` 布尔列（空间级
  推测层开关，默认 False；有效开关 = 平台 env AND 本列，fail-closed）。

说明：推测边是显示层投影，永不写 SourceFact；确认走 relationship_proposals
现行 consent 合同。SQLite 迁移按非事务 DDL 处理；downgrade 结构还原。

Revision ID: 0042_steward_inferred_edges
Revises: 0041_term_pack_expansion
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_steward_inferred_edges"
down_revision: str | None = "0041_term_pack_expansion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "steward_inferred_edges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "object_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "relation_kind",
            sa.String(48),
            sa.CheckConstraint(
                "relation_kind IN ('biological_parent','adoptive_parent','step_parent',"
                "'guardian','spouse','partner','direct_sibling')",
                name="ck_sie_kind",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('proposed','rejected','confirmed','superseded')",
                name="ck_sie_status",
            ),
            server_default="proposed",
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.String(16),
            sa.CheckConstraint("origin IN ('llm','rule','intake')", name="ck_sie_origin"),
            server_default="llm",
            nullable=False,
        ),
        sa.Column(
            "source_candidate_id",
            sa.Integer(),
            sa.ForeignKey("steward_llm_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("subject_user_id <> object_user_id", name="ck_sie_distinct_endpoints"),
    )
    op.create_index(
        "uq_sie_active_triple",
        "steward_inferred_edges",
        ["space_id", "subject_user_id", "object_user_id", "relation_kind"],
        unique=True,
        sqlite_where=sa.text("status = 'proposed'"),
    )
    op.create_index("ix_sie_space_status", "steward_inferred_edges", ["space_id", "status"])
    op.create_index(
        "ix_sie_endpoints", "steward_inferred_edges", ["subject_user_id", "object_user_id"]
    )
    op.create_index("ix_sie_candidate", "steward_inferred_edges", ["source_candidate_id"])

    with op.batch_alter_table("agent_space_provider_settings") as batch:
        batch.add_column(
            sa.Column(
                "inferred_tree",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_space_provider_settings") as batch:
        batch.drop_column("inferred_tree")
    op.drop_index("ix_sie_candidate", table_name="steward_inferred_edges")
    op.drop_index("ix_sie_endpoints", table_name="steward_inferred_edges")
    op.drop_index("ix_sie_space_status", table_name="steward_inferred_edges")
    op.drop_index("uq_sie_active_triple", table_name="steward_inferred_edges")
    op.drop_table("steward_inferred_edges")
