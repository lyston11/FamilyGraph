"""steward_terminology：称谓自主优化持久化与治理（09-13-steward-terminology-autonomy）。

纯增量：
- 新表 steward_term_projections / steward_term_suppressions；
- steward_model_calls 加 viewer_account_id（旧三类保持 NULL）并把 kind CHECK
  扩展为含 terminology（SQLite batch recreate 重建约束）；
- steward_space_schedules 加 assist_kind_cursor（每空间 kind 轮转进度，默认 0）；
- steward_suggestion_recipients 加 preference_feedback/preference_at；
- agent_space_provider_settings 加 assist_terminology（默认关）；
- platform_feature_configs 加 steward_assist_terminology（默认关，DB ∧ env 治理）。

不改旧迁移、不为旧三 kind 回填 viewer。Revision 0044 在串行集成点分配。

Revision ID: 0044_steward_terminology
Revises: 0043_platform_steward_assist_switches
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_steward_terminology"
down_revision: str | None = "0043_platform_steward_assist_switches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "steward_term_projections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "viewer_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "root_user_id",
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
        sa.Column("concept_code", sa.String(length=64), nullable=True),
        sa.Column("semantic_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("baseline_term", sa.String(length=64), nullable=True),
        sa.Column("baseline_source", sa.String(length=32), nullable=True),
        sa.Column("term", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=True),
        sa.Column("source_model_call_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_checked_hash", sa.String(length=64), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_attempt_status", sa.String(length=16), nullable=True),
        sa.Column("suppression_key", sa.String(length=64), nullable=True),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','suppressed','stale','unchanged')", name="ck_stp_status"
        ),
        sa.CheckConstraint("origin IN ('deterministic','model')", name="ck_stp_origin"),
        sa.UniqueConstraint(
            "space_id",
            "viewer_account_id",
            "root_user_id",
            "target_user_id",
            name="uq_stp_scope",
        ),
    )
    op.create_index("ix_stp_semantic", "steward_term_projections", ["viewer_account_id", "semantic_hash"])
    op.create_index("ix_stp_checked", "steward_term_projections", ["last_checked_hash"])

    op.create_table(
        "steward_term_suppressions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "viewer_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suppression_key", sa.String(length=64), nullable=False),
        sa.Column("source_suggestion_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "viewer_account_id",
            "space_id",
            "target_user_id",
            "suppression_key",
            name="uq_sts_key",
        ),
    )

    with op.batch_alter_table("steward_model_calls") as batch:
        batch.add_column(
            sa.Column(
                "viewer_account_id",
                sa.Integer(),
                sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                nullable=True,
            )
        )
        batch.drop_constraint("ck_smc_assist_kind", type_="check")
        batch.create_check_constraint(
            "ck_smc_assist_kind",
            "assist_kind IN ('candidate','ranking','explanation','terminology')",
        )

    with op.batch_alter_table("steward_space_schedules") as batch:
        batch.add_column(
            sa.Column(
                "assist_kind_cursor", sa.Integer(), nullable=False, server_default="0"
            )
        )

    with op.batch_alter_table("steward_suggestion_recipients") as batch:
        batch.add_column(sa.Column("preference_feedback", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("preference_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("agent_space_provider_settings") as batch:
        batch.add_column(
            sa.Column(
                "assist_terminology", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    with op.batch_alter_table("platform_feature_configs") as batch:
        batch.add_column(
            sa.Column(
                "steward_assist_terminology",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("platform_feature_configs") as batch:
        batch.drop_column("steward_assist_terminology")
    with op.batch_alter_table("agent_space_provider_settings") as batch:
        batch.drop_column("assist_terminology")
    with op.batch_alter_table("steward_suggestion_recipients") as batch:
        batch.drop_column("preference_at")
        batch.drop_column("preference_feedback")
    with op.batch_alter_table("steward_space_schedules") as batch:
        batch.drop_column("assist_kind_cursor")
    with op.batch_alter_table("steward_model_calls") as batch:
        batch.drop_constraint("ck_smc_assist_kind", type_="check")
        batch.create_check_constraint(
            "ck_smc_assist_kind", "assist_kind IN ('candidate','ranking','explanation')"
        )
        batch.drop_column("viewer_account_id")
    op.drop_table("steward_term_suppressions")
    op.drop_index("ix_stp_checked", table_name="steward_term_projections")
    op.drop_index("ix_stp_semantic", table_name="steward_term_projections")
    op.drop_table("steward_term_projections")
