"""Steward 建议审核投影（任务 09-11-steward-candidate-review）。

两张新表：
1. ``steward_suggestions``：有证据的模型候选 / 确定性冲突待办的受控审核投影。
   去重键 = space + kind + 有向端点 + 结构化建议值（+ term_preference 的 viewer
   account），不含模型措辞；evidence_hash 单独成列，证据变化 → 新建议并
   supersede 旧活动行。部分唯一索引 uq_steward_suggestions_active_dedupe
   保证并发生成在同一 (space, dedupe_key, evidence_hash) 上收敛为一行。
2. ``steward_suggestion_recipients``：按收件人账号的已读/驳回/冷却独立状态
   （UNIQUE (suggestion_id, account_id)）。

downgrade：drop 两表（先收件表）。历史内部候选行（steward_llm_candidates）
保留不动；停用审核 UI/API 后核心卡片仍正常。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_steward_suggestions"
down_revision: str | None = "0037_steward_assist_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_STATUS_SQL = "status IN ('proposed','submitted')"


def upgrade() -> None:
    op.create_table(
        "steward_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("space_id", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject_user_id", sa.Integer(), nullable=False),
        sa.Column("object_user_id", sa.Integer(), nullable=True),
        sa.Column("viewer_account_id", sa.Integer(), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="proposed", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("source_candidate_id", sa.Integer(), nullable=True),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("linked_fact_id", sa.Integer(), nullable=True),
        sa.Column("linked_term_id", sa.Integer(), nullable=True),
        sa.Column("submit_key", sa.String(120), nullable=True),
        sa.Column("submit_result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("origin IN ('deterministic','model')", name="ck_ss_origin"),
        sa.CheckConstraint(
            "kind IN ('relation_proposal','term_preference','identity_duplicate',"
            "'missing_information')",
            name="ck_ss_kind",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','submitted','resolved','expired','superseded')",
            name="ck_ss_status",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["family_spaces.id"], name="fk_ss_space", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"], ["users.id"], name="fk_ss_subject", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["object_user_id"], ["users.id"], name="fk_ss_object", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["viewer_account_id"],
            ["accounts.id"],
            name="fk_ss_viewer_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_candidate_id"],
            ["steward_llm_candidates.id"],
            name="fk_ss_candidate",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id"], ["steward_jobs.id"], name="fk_ss_job", ondelete="SET NULL"
        ),
        sa.Index(
            "uq_steward_suggestions_active_dedupe",
            "space_id",
            "dedupe_key",
            "evidence_hash",
            unique=True,
            sqlite_where=sa.text(_ACTIVE_STATUS_SQL),
        ),
        sa.Index("ix_steward_suggestions_space_status", "space_id", "status"),
        sa.Index("ix_steward_suggestions_subject", "subject_user_id"),
        sa.Index("ix_steward_suggestions_object", "object_user_id"),
    )
    op.create_table(
        "steward_suggestion_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("suggestion_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("cooldown_evidence_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["suggestion_id"],
            ["steward_suggestions.id"],
            name="fk_ssr_suggestion",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_ssr_account", ondelete="CASCADE"
        ),
        sa.Index(
            "uq_steward_suggestion_recipients",
            "suggestion_id",
            "account_id",
            unique=True,
        ),
        sa.Index("ix_steward_suggestion_recipients_account", "account_id"),
    )
    # 通知扩展：suggestion 引用 + 收件人去重唯一（unique (recipient, space, suggestion)）
    # kind CHECK 需扩入 'steward_suggestion'（SQLite 重建表替换约束）
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("suggestion_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notifications_suggestion",
            "steward_suggestions",
            ["suggestion_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint(
            "ck_notifications_kind",
            "kind IN ('action_card','space_membership','bridge','relation','steward_suggestion')",
        )
    op.create_index(
        "uq_notifications_suggestion",
        "notifications",
        ["recipient_account_id", "space_id", "suggestion_id"],
        unique=True,
        sqlite_where=sa.text("suggestion_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 建议通知的 kind 不在旧 CHECK 集合内：回滚是应急路径，先删除这类通知行
    # （建议本身可由 Steward 重算重新投影，通知为可再生状态；删除仅发生于
    # downgrade，不影响 forward 路径的数据）。
    op.execute("DELETE FROM notifications WHERE kind = 'steward_suggestion'")
    op.drop_index("uq_notifications_suggestion", table_name="notifications")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("fk_notifications_suggestion", type_="foreignkey")
        batch.drop_column("suggestion_id")
        batch.drop_constraint("ck_notifications_kind", type_="check")
        batch.create_check_constraint(
            "ck_notifications_kind",
            "kind IN ('action_card','space_membership','bridge','relation')",
        )
    op.drop_table("steward_suggestion_recipients")
    op.drop_table("steward_suggestions")
