"""derived_facts：确定性亲属路径 DerivedFact 缓存（V2.3 Block E2，KI-2）。

变更总览（task 08-26-v2-3-relationship-intelligence design.md 模型节）：
- derived_facts：(viewer, target, space) 唯一的可重建缓存行。concept_code 由
  确定性 resolver 从 confirmed SourceFact 路径编码（编码合同见
  services/relationship_resolver.py 模块 docstring）；main/alt path 存规范化
  step 序列 JSON；evidence_hash = sha256(snapshot_hash + algorithm_version)
  是缓存新鲜度判据——SourceFact revision/state 变化改变 snapshot_hash，旧缓存
  自然失效（AC-KI7/8）。行本体可随时删除重建，不是真源。
- term_version 可空：E3 TermRegistry 接入后填充；E2 不写该列。

说明：SQLite 迁移按非事务 DDL 处理；downgrade 仅结构还原。

Revision ID: 0011_derived_facts
Revises: 0010_relationship_facts
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_derived_facts"
down_revision: str | None = "0010_relationship_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "derived_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "viewer_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_user_id",
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
        sa.Column("concept_code", sa.String(128), nullable=False),
        sa.Column("main_path_json", sa.JSON(), nullable=False),
        sa.Column("alt_paths_json", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("evidence_fact_ids_json", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(16), nullable=False),
        sa.Column("term_version", sa.String(16), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_derived_facts_viewer_target_space",
        "derived_facts",
        ["viewer_user_id", "target_user_id", "space_id"],
        unique=True,
    )
    op.create_index("ix_derived_facts_viewer", "derived_facts", ["viewer_user_id"])
    op.create_index("ix_derived_facts_target", "derived_facts", ["target_user_id"])
    op.create_index("ix_derived_facts_space", "derived_facts", ["space_id"])


def downgrade() -> None:
    op.drop_index("ix_derived_facts_space", table_name="derived_facts")
    op.drop_index("ix_derived_facts_target", table_name="derived_facts")
    op.drop_index("ix_derived_facts_viewer", table_name="derived_facts")
    op.drop_index("uq_derived_facts_viewer_target_space", table_name="derived_facts")
    op.drop_table("derived_facts")
