"""空间管理员申请表（平台运营者审批制，任务 08-30-space-manager-approval）。

- 申请类型只有 ``space_admin``：active member 申请升级为目标空间管理员。
- 同 (applicant, space, kind) 至多一条 pending，裁决后保留历史记录。
- 现有空间 owner 不受影响：family_spaces.owner_id 仅经 ownership_transfers FSM 变更。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_space_manager_application"
down_revision: str | None = "0020_agent_runtime_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "space_manager_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "applicant_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "decided_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("request_kind IN ('space_admin')", name="ck_sma_kind"),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_sma_status"),
        sa.CheckConstraint("space_id IS NOT NULL", name="ck_sma_space_required"),
    )
    op.create_index(
        "uq_space_manager_application_pending",
        "space_manager_applications",
        ["applicant_user_id", "space_id", "request_kind"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_space_manager_applications_applicant",
        "space_manager_applications",
        ["applicant_user_id"],
    )
    op.create_index(
        "ix_space_manager_applications_space",
        "space_manager_applications",
        ["space_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_space_manager_applications_space", table_name="space_manager_applications")
    op.drop_index(
        "ix_space_manager_applications_applicant", table_name="space_manager_applications"
    )
    op.drop_index("uq_space_manager_application_pending", table_name="space_manager_applications")
    op.drop_table("space_manager_applications")
