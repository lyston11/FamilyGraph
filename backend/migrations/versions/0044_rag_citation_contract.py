"""B: citation/attempt contract for runs, events and context builds.

Revision ID: 0044_rag_citation_contract
Revises: 0042_memory_source_contract, 0043_platform_steward_assist_switches
Create Date: 2026-09-13

Workstream B (MR-03/04/09/14). Adds:
- agent_run_events.request_fingerprint: canonical idempotency fingerprint of
  the sidecar's original request (never the server-authenticated result).
- agent_run_events.context_reference_json: bounded internal record
  {context_build_id, attempt, used_handles}; never in public_payload.
- context_builds.attempt (+unique index with run_id): one valid build per
  attempt; replayed context GETs re-authorize instead of re-competing.
- context_builds.blocks_json: authorized block payload stored once so the
  replay returns the identical context.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0044_rag_citation_contract"
down_revision: tuple[str, str] | None = (
    "0042_memory_source_contract",
    "0043_platform_steward_assist_switches",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_run_events",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_run_events",
        sa.Column("context_reference_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "context_builds",
        sa.Column("attempt", sa.Integer(), nullable=True),
    )
    op.add_column(
        "context_builds",
        sa.Column("blocks_json", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_context_builds_run_attempt",
        "context_builds",
        ["run_id", "attempt"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_context_builds_run_attempt", table_name="context_builds")
    op.drop_column("context_builds", "blocks_json")
    op.drop_column("context_builds", "attempt")
    op.drop_column("agent_run_events", "context_reference_json")
    op.drop_column("agent_run_events", "request_fingerprint")
