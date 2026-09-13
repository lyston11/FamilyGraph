"""09-13 steward short-tx: generation progress, per-view progress, retry budgets.

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-14

加法迁移（design §8）：三张新表——
- steward_generations：空间重算发布代次（running/published/failed/superseded）；
- steward_generation_views：代次内单查看者真实进度（ready/failed + 计数）；
- steward_retry_budgets：按输入指纹跨代持久的重试预算。
不触碰既有表与数据；downgrade 仅删新表。旧 DerivedFact/PFV 缓存语义不变
（evidence_hash 指纹继续守护），无需整体失效。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0044_steward_generations"
down_revision: str | None = "0043_platform_steward_assist_switches"
branch_labels = None
depends_on = None

_GENERATION_STATUS_SQL = "status IN ('running','published','failed','superseded')"
_GENERATION_VIEW_STATUS_SQL = "status IN ('pending','ready','failed')"


def upgrade() -> None:
    op.create_table(
        "steward_generations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("steward_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("execution_cursor", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("stats_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(_GENERATION_STATUS_SQL, name="ck_sg_status"),
    )
    op.create_index("ix_sg_space_created", "steward_generations", ["space_id", "created_at"])
    op.create_index("ix_sg_job", "steward_generations", ["job_id"])

    op.create_table(
        "steward_generation_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "generation_id",
            sa.Integer(),
            sa.ForeignKey("steward_generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(_GENERATION_VIEW_STATUS_SQL, name="ck_sgv_status"),
        sa.UniqueConstraint("generation_id", "viewer_account_id", name="uq_sgv_gen_viewer"),
    )
    op.create_index("ix_sgv_viewer", "steward_generation_views", ["viewer_account_id", "space_id"])

    op.create_table(
        "steward_retry_budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=48), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("exhausted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("space_id", "fingerprint", "scope", name="uq_srb_scope"),
    )
    op.create_index("ix_srb_space", "steward_retry_budgets", ["space_id"])


def downgrade() -> None:
    op.drop_index("ix_srb_space", table_name="steward_retry_budgets")
    op.drop_table("steward_retry_budgets")
    op.drop_index("ix_sgv_viewer", table_name="steward_generation_views")
    op.drop_table("steward_generation_views")
    op.drop_index("ix_sg_job", table_name="steward_generations")
    op.drop_index("ix_sg_space_created", table_name="steward_generations")
    op.drop_table("steward_generations")
