"""PersonalFamilyView snapshots and explicit cross-lineage bridge consents."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_personal_family_view"
down_revision: str | None = "0024_agent_runtime_assistant_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_family_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("viewer_account_id", sa.Integer(), nullable=False),
        sa.Column("root_user_id", sa.Integer(), nullable=False),
        sa.Column("space_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="never_computed"),
        sa.Column("view_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False, server_default="v1"),
        sa.Column("computation_version", sa.String(length=64), nullable=False, server_default="pfv-v1"),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("failed_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('never_computed','queued','running','current','stale','failed')",
            name="ck_pfv_status",
        ),
        sa.ForeignKeyConstraint(["viewer_account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["root_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["family_spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_pfv_scope",
        "personal_family_views",
        ["viewer_account_id", "root_user_id", "space_id"],
        unique=True,
    )
    op.create_index("ix_pfv_space_status", "personal_family_views", ["space_id", "status"])
    op.create_index("ix_pfv_viewer_space", "personal_family_views", ["viewer_account_id", "space_id"])

    op.create_table(
        "personal_family_view_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("view_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("display_json", sa.JSON(), nullable=False),
        sa.Column("visibility_level", sa.String(length=20), nullable=False),
        sa.Column("inclusion_reason_code", sa.String(length=64), nullable=False),
        sa.Column("source_fact_ids_json", sa.JSON(), nullable=False),
        sa.Column("authorization_basis_json", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("computation_version", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "visibility_level IN ('self_private','household_detail','lineage_summary')",
            name="ck_pfv_node_visibility",
        ),
        sa.ForeignKeyConstraint(["view_id"], ["personal_family_views.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_pfv_node_view_user",
        "personal_family_view_nodes",
        ["view_id", "user_id"],
        unique=True,
    )
    op.create_index("ix_pfv_node_user", "personal_family_view_nodes", ["user_id"])

    op.create_table(
        "personal_family_view_edges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("view_id", sa.Integer(), nullable=False),
        sa.Column("from_user_id", sa.Integer(), nullable=False),
        sa.Column("to_user_id", sa.Integer(), nullable=False),
        sa.Column("edge_kind", sa.String(length=32), nullable=False),
        sa.Column("source_fact_id", sa.Integer(), nullable=True),
        sa.Column("path_json", sa.JSON(), nullable=False),
        sa.Column("alternative_paths_json", sa.JSON(), nullable=False),
        sa.Column("path_class", sa.String(length=32), nullable=False),
        sa.Column("concept_code", sa.String(length=120), nullable=True),
        sa.Column("term", sa.String(length=120), nullable=True),
        sa.Column("inclusion_reason_code", sa.String(length=64), nullable=False),
        sa.Column("authorization_basis_json", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("computation_version", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["view_id"], ["personal_family_views.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_fact_id"], ["source_facts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pfv_edge_view", "personal_family_view_edges", ["view_id"])
    op.create_index(
        "ix_pfv_edge_endpoints",
        "personal_family_view_edges",
        ["from_user_id", "to_user_id"],
    )

    op.create_table(
        "personal_family_bridges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lineage_space_a_id", sa.Integer(), nullable=False),
        sa.Column("lineage_space_b_id", sa.Integer(), nullable=False),
        sa.Column("anchor_a_user_id", sa.Integer(), nullable=False),
        sa.Column("anchor_b_user_id", sa.Integer(), nullable=False),
        sa.Column("normalized_pair_key", sa.String(length=255), nullable=False),
        sa.Column("initiated_by_account_id", sa.Integer(), nullable=False),
        sa.Column("consent_a_account_id", sa.Integer(), nullable=True),
        sa.Column("consent_b_account_id", sa.Integer(), nullable=True),
        sa.Column("consent_a_at", sa.DateTime(), nullable=True),
        sa.Column("consent_b_at", sa.DateTime(), nullable=True),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','active','revoked','expired','rejected')",
            name="ck_pfb_status",
        ),
        sa.CheckConstraint("lineage_space_a_id <> lineage_space_b_id", name="ck_pfb_distinct_spaces"),
        sa.CheckConstraint("anchor_a_user_id <> anchor_b_user_id", name="ck_pfb_distinct_anchors"),
        sa.ForeignKeyConstraint(["lineage_space_a_id"], ["family_spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lineage_space_b_id"], ["family_spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["anchor_a_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["anchor_b_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiated_by_account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["consent_a_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["consent_b_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_pfb_normalized_pair",
        "personal_family_bridges",
        ["normalized_pair_key"],
        unique=True,
    )
    op.create_index(
        "ix_pfb_space_status",
        "personal_family_bridges",
        ["lineage_space_a_id", "status"],
    )
    op.create_index(
        "ix_pfb_anchor_status",
        "personal_family_bridges",
        ["anchor_a_user_id", "anchor_b_user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_pfb_anchor_status", table_name="personal_family_bridges")
    op.drop_index("ix_pfb_space_status", table_name="personal_family_bridges")
    op.drop_index("uq_pfb_normalized_pair", table_name="personal_family_bridges")
    op.drop_table("personal_family_bridges")
    op.drop_index("ix_pfv_edge_endpoints", table_name="personal_family_view_edges")
    op.drop_index("ix_pfv_edge_view", table_name="personal_family_view_edges")
    op.drop_table("personal_family_view_edges")
    op.drop_index("ix_pfv_node_user", table_name="personal_family_view_nodes")
    op.drop_index("uq_pfv_node_view_user", table_name="personal_family_view_nodes")
    op.drop_table("personal_family_view_nodes")
    op.drop_index("ix_pfv_viewer_space", table_name="personal_family_views")
    op.drop_index("ix_pfv_space_status", table_name="personal_family_views")
    op.drop_index("uq_pfv_scope", table_name="personal_family_views")
    op.drop_table("personal_family_views")
