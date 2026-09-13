"""platform_steward_assist_switches：平台级 Steward 辅助开关纳入治理（09-13）。

platform_feature_configs 单例新增三列 steward_assist_candidate/ranking/
explanation（与 memory_enabled/rag_enabled 同一治理面：DB ∧ 环境部署兜底；
行缺失 = 环境回退，env 仍为初始值/紧急通道）。纯增量加列，不触碰既有行。

Revision ID: 0043_platform_steward_assist_switches
Revises: 0042_steward_inferred_edges
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_platform_steward_assist_switches"
down_revision: str | None = "0042_steward_inferred_edges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("platform_feature_configs") as batch:
        batch.add_column(
            sa.Column(
                "steward_assist_candidate",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "steward_assist_ranking",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "steward_assist_explanation",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("platform_feature_configs") as batch:
        batch.drop_column("steward_assist_explanation")
        batch.drop_column("steward_assist_ranking")
        batch.drop_column("steward_assist_candidate")
