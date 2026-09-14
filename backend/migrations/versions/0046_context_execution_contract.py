"""B: persistent context invalidation and execution policy evidence.

Revision ID: 0046_context_execution_contract
Revises: 0045_rag_index_lifecycle
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_context_execution_contract"
down_revision = "0045_rag_index_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL means historical/unproven. Never infer evidence from current data
    # or delete legacy block snapshots while introducing the new contract.
    op.add_column("context_builds", sa.Column("policy_json", sa.JSON(), nullable=True))
    op.add_column("context_builds", sa.Column("invalidated_at", sa.DateTime(), nullable=True))
    op.add_column("context_builds", sa.Column("invalidation_reason", sa.String(64), nullable=True))


def downgrade() -> None:
    # Older code cannot honor the permanent invalidation fence. Refuse before
    # losing any evidence when an execution has entered the new contract.
    bind = op.get_bind()
    if (
        bind.execute(
            sa.text(
                "SELECT 1 FROM context_builds WHERE policy_json IS NOT NULL "
                "OR invalidated_at IS NOT NULL LIMIT 1"
            )
        ).first()
        is not None
    ):
        raise RuntimeError("context execution evidence exists; retain data and roll forward")
    op.drop_column("context_builds", "invalidation_reason")
    op.drop_column("context_builds", "invalidated_at")
    op.drop_column("context_builds", "policy_json")
