"""Create the platform-level Memory/RAG feature switch singleton table.

The table is intentionally empty after migration: an empty table means the
legacy environment values remain the bootstrap source until an administrator
saves explicit platform values.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_platform_feature_configs"
down_revision = "0038_steward_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_feature_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rag_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_system_admin_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_platform_feature_config_singleton"),
        sa.ForeignKeyConstraint(
            ["updated_by_system_admin_id"],
            ["system_admins.id"],
            name="fk_platform_feature_configs_updated_by_system_admin_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("platform_feature_configs")
