"""Add Pi adapter metadata and immutable run runtime snapshots.

The database is pre-deployment, but the migration is additive so existing
installations keep their provider and run rows.  Secrets remain encrypted in
the existing column and are never copied to the snapshot.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_agent_runtime_profile"
down_revision: str | None = "0019_web_approved_use_case"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_providers",
        sa.Column("api", sa.String(length=48), nullable=False, server_default="openai-responses"),
    )
    op.add_column(
        "agent_providers",
        sa.Column("compat_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "agent_providers",
        sa.Column("context_window", sa.Integer(), nullable=False, server_default="272000"),
    )
    op.add_column(
        "agent_providers",
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="60000"),
    )
    op.add_column(
        "agent_providers",
        sa.Column("reasoning", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "agent_providers",
        sa.Column(
            "input_modalities_json",
            sa.JSON(),
            nullable=False,
            server_default='["text","image"]',
        ),
    )
    op.add_column(
        "agent_providers",
        sa.Column(
            "thinking_levels_json",
            sa.JSON(),
            nullable=False,
            server_default='["low","medium","high","xhigh","max"]',
        ),
    )
    op.add_column("agent_runs", sa.Column("runtime_snapshot_json", sa.JSON(), nullable=True))
    op.add_column(
        "agent_sessions",
        sa.Column("term_usage_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "runtime_snapshot_json")
    op.drop_column("agent_sessions", "term_usage_consent")
    op.drop_column("agent_providers", "compat_json")
    op.drop_column("agent_providers", "thinking_levels_json")
    op.drop_column("agent_providers", "input_modalities_json")
    op.drop_column("agent_providers", "reasoning")
    op.drop_column("agent_providers", "max_tokens")
    op.drop_column("agent_providers", "context_window")
    op.drop_column("agent_providers", "api")
