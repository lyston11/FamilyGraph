"""steward_actioncard：behavior_projections / action_cards / steward_jobs（V2.4 S1）。

- behavior_projections：空间×账号行为投影（词条使用/卡片冷却/纠正偏好计数）。
- action_cards：有状态推荐卡；partial unique index 保证同
  (space, dedupe_key, evidence_version) 至多一张活动卡（AC-ST3）。
- steward_jobs：每空间至多一个活跃作业（partial unique index）；
  checkpoint 只存进度/版本。

说明：SQLite 迁移按非事务 DDL 处理；downgrade 仅结构还原。

Revision ID: 0013_steward_action_card
Revises: 0012_term_registry
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_steward_action_card"
down_revision: str | None = "0012_term_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "behavior_projections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("projection_key", sa.String(160), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_behavior_projections_key",
        "behavior_projections",
        ["space_id", "account_id", "projection_key"],
        unique=True,
    )
    op.create_index("ix_behavior_projections_account", "behavior_projections", ["account_id"])

    op.create_table(
        "action_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kind",
            sa.String(32),
            sa.CheckConstraint("kind IN ('household_link','lineage_request')", name="ck_ac_kind"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
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
            nullable=True,
        ),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("evidence_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("proposed_action_json", sa.JSON(), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("privacy_effect", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.String(16),
            sa.CheckConstraint(
                "state IN ('pending','viewed','accepted','executed','dismissed',"
                "'expired','superseded')",
                name="ck_ac_state",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("executed_event_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("failed_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_action_cards_active_dedupe",
        "action_cards",
        ["space_id", "dedupe_key", "evidence_version"],
        unique=True,
        sqlite_where=sa.text("state IN ('pending','viewed','accepted')"),
    )
    op.create_index("ix_action_cards_space_state", "action_cards", ["space_id", "state"])
    op.create_index("ix_action_cards_recipient", "action_cards", ["recipient_account_id", "state"])
    op.create_index("ix_action_cards_subject", "action_cards", ["subject_user_id"])
    op.create_index("ix_action_cards_object", "action_cards", ["object_user_id"])

    op.create_table(
        "steward_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cause",
            sa.String(24),
            sa.CheckConstraint(
                "cause IN ('source_fact','claim','membership','term','disclosure',"
                "'domain_event','integrity_scan','admin_rerun')",
                name="ck_sj_cause",
            ),
            nullable=False,
        ),
        sa.Column("trigger_cursor", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('queued','leased','running','succeeded','failed','expired')",
                name="ck_sj_status",
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("last_event_cursor", sa.Integer(), nullable=True),
        sa.Column("checkpoint_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("leased_by", sa.String(120), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uq_steward_jobs_space_active",
        "steward_jobs",
        ["space_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued','leased','running')"),
    )
    op.create_index("ix_steward_jobs_lease_scan", "steward_jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_steward_jobs_lease_scan", table_name="steward_jobs")
    op.drop_index("uq_steward_jobs_space_active", table_name="steward_jobs")
    op.drop_table("steward_jobs")
    op.drop_index("ix_action_cards_object", table_name="action_cards")
    op.drop_index("ix_action_cards_subject", table_name="action_cards")
    op.drop_index("ix_action_cards_recipient", table_name="action_cards")
    op.drop_index("ix_action_cards_space_state", table_name="action_cards")
    op.drop_index("uq_action_cards_active_dedupe", table_name="action_cards")
    op.drop_table("action_cards")
    op.drop_index("ix_behavior_projections_account", table_name="behavior_projections")
    op.drop_index("uq_behavior_projections_key", table_name="behavior_projections")
    op.drop_table("behavior_projections")
