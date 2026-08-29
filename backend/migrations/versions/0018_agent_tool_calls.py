"""agent_tool_calls：工具调用副作用去重台账（R4）。

同 (run_id, tool_call_id) 至多一行（tool_call_id 为 NULL 不参与唯一约束）；
重放同 id 幂等返回首次输出，不同工具/版本 → 409 AGENT_TOOL_CALL_CONFLICT。

Revision ID: 0018_agent_tool_calls
Revises: 0017_export_envelope
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_agent_tool_calls"
down_revision: str | None = "0017_export_envelope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Index(
            "uq_agent_tool_calls_run_call",
            "run_id",
            "tool_call_id",
            unique=True,
            sqlite_where=sa.text("tool_call_id IS NOT NULL"),
        ),
        sa.Index("ix_agent_tool_calls_run_id", "run_id"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_tool_calls_run_id", table_name="agent_tool_calls")
    op.drop_index("uq_agent_tool_calls_run_call", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
