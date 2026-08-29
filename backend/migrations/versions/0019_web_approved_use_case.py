"""web_approved_urls.use_case：批准凭据记录签发时的联网用途。

fetch_approved_page 按凭据的 use_case（而非固定 research）取 policy，
fact_check/citation 的配额与上限语义随之正确（P1 整改）。

Revision ID: 0019_web_approved_use_case
Revises: 0018_agent_tool_calls
Create Date: 2026-08-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_web_approved_use_case"
down_revision: str | None = "0018_agent_tool_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_approved_urls",
        sa.Column(
            "use_case",
            sa.String(length=32),
            nullable=False,
            server_default="research",
        ),
    )


def downgrade() -> None:
    op.drop_column("web_approved_urls", "use_case")
