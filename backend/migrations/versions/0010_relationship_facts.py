"""relationship_facts：SourceFact / SocialRelation / RawRelationInput（V2.3 Block E1）。

变更总览（task 08-26-v2-3-relationship-intelligence design.md 模型节）：
- source_facts：稳定原子亲属事实（KI-1）。fact_type CHECK 七类；同
  (subject, object, fact_type, space_id) 至多一条非 revoked 行（partial
  unique index，NULL space 用 COALESCE 归一）；自环 CHECK；parent 类成环
  检测在服务层（≤32 层），数据库不做。
- social_relations：friend/colleague 等社会关系，单独存储，不参加血缘路径。
- raw_relation_inputs：自由输入原文（KI-3）。append-only——BEFORE UPDATE
  触发器无条件 ABORT，任何词典/Agent 产物不得覆盖原文。

说明：SQLite 迁移按非事务 DDL 处理；downgrade 仅结构还原。

Revision ID: 0010_relationship_facts
Revises: 0009_agent_runtime
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_relationship_facts"
down_revision: str | None = "0009_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FACT_TYPE_CHECK = (
    "fact_type IN ('biological_parent','adoptive_parent','step_parent',"
    "'guardian','spouse','partner','direct_sibling')"
)
_STATE_CHECK = "state IN ('proposed','confirmed','disputed','revoked')"
_PROVENANCE_CHECK = (
    "provenance IN ('profile_form','connection_accept','manual_entry','import','agent_proposal')"
)
_KIND_CHECK = "relation_kind IN ('friend','colleague','acquaintance','other')"

_RAW_IMMUTABLE_TRIGGER_SQL = """
CREATE TRIGGER trg_raw_relation_inputs_immutable
BEFORE UPDATE ON raw_relation_inputs
BEGIN
    SELECT RAISE(ABORT, 'raw_relation_inputs is append-only');
END;
"""


def upgrade() -> None:
    # ---- 1. 自由输入原文（append-only，先建供 source_facts 引用）----
    op.create_table(
        "raw_relation_inputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "author_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(200), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.execute(sa.text(_RAW_IMMUTABLE_TRIGGER_SQL))

    # ---- 2. SourceFact 原子亲属事实 ----
    op.create_table(
        "source_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fact_type",
            sa.String(32),
            sa.CheckConstraint(_FACT_TYPE_CHECK, name="ck_sf_fact_type"),
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
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "asserted_by_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "provenance",
            sa.String(20),
            sa.CheckConstraint(_PROVENANCE_CHECK, name="ck_sf_provenance"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(16),
            sa.CheckConstraint(_STATE_CHECK, name="ck_sf_state"),
            server_default="proposed",
            nullable=False,
        ),
        sa.Column(
            "raw_text_id",
            sa.Integer(),
            sa.ForeignKey("raw_relation_inputs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("subject_user_id != object_user_id", name="ck_sf_no_self"),
    )
    op.create_index("ix_source_facts_subject", "source_facts", ["subject_user_id"])
    op.create_index("ix_source_facts_object", "source_facts", ["object_user_id"])
    op.create_index("ix_source_facts_space_state", "source_facts", ["space_id", "state"])
    op.create_index(
        "uq_source_facts_active",
        "source_facts",
        ["subject_user_id", "object_user_id", "fact_type", sa.text("COALESCE(space_id, -1)")],
        unique=True,
        sqlite_where=sa.text("state != 'revoked'"),
    )

    # ---- 3. 社会关系（不参加血缘/姻亲路径）----
    op.create_table(
        "social_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "relation_kind",
            sa.String(20),
            sa.CheckConstraint(_KIND_CHECK, name="ck_sr_kind"),
            nullable=False,
        ),
        sa.Column(
            "user_a_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_b_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("user_a_id != user_b_id", name="ck_sr_no_self"),
    )
    op.create_index("ix_social_relations_user_a", "social_relations", ["user_a_id"])
    op.create_index("ix_social_relations_user_b", "social_relations", ["user_b_id"])


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_raw_relation_inputs_immutable"))
    op.drop_index("ix_social_relations_user_b", table_name="social_relations")
    op.drop_index("ix_social_relations_user_a", table_name="social_relations")
    op.drop_table("social_relations")
    op.drop_index("uq_source_facts_active", table_name="source_facts")
    op.drop_index("ix_source_facts_space_state", table_name="source_facts")
    op.drop_index("ix_source_facts_object", table_name="source_facts")
    op.drop_index("ix_source_facts_subject", table_name="source_facts")
    op.drop_table("source_facts")
    op.drop_table("raw_relation_inputs")
