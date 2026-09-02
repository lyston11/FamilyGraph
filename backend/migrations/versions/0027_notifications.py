"""Recipient-addressed minimal notification projection rows."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_notifications"
down_revision: str | None = "0026_remove_guest_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("space_id", sa.Integer(), nullable=False),
        sa.Column("recipient_account_id", sa.Integer(), nullable=False),
        sa.Column("action_card_id", sa.Integer(), nullable=True),
        sa.Column("space_member_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('action_card','space_membership','bridge','relation')",
            name="ck_notifications_kind",
        ),
        sa.CheckConstraint(
            "kind <> 'action_card' OR action_card_id IS NOT NULL",
            name="ck_notifications_action_card_ref",
        ),
        sa.ForeignKeyConstraint(["space_id"], ["family_spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["recipient_account_id"], ["accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["action_card_id"], ["action_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_member_id"], ["space_members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_recipient_space_read",
        "notifications",
        ["recipient_account_id", "space_id", "read_at"],
    )
    op.create_index("ix_notifications_space", "notifications", ["space_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_space", table_name="notifications")
    op.drop_index("ix_notifications_recipient_space_read", table_name="notifications")
    op.drop_table("notifications")
