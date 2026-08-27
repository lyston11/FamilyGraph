"""V2.6 controlled web configuration, approvals, usage and citations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_controlled_web"
down_revision: str | None = "0014_memory_rag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_platform_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("search_provider", sa.String(64), nullable=False, server_default="configured"),
        sa.Column("search_endpoint", sa.String(500), nullable=True),
        sa.Column("provider_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("allowed_domains_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("denied_domains_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_fetch_bytes", sa.Integer(), nullable=False, server_default="1000000"),
        sa.Column("max_requests_per_minute", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("monthly_budget_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "updated_by_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("id = 1", name="ck_web_platform_config_singleton"),
    )
    op.create_table(
        "web_space_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_use_cases_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_fetch_bytes", sa.Integer(), nullable=False, server_default="1000000"),
        sa.Column("max_requests_per_minute", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("monthly_budget_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "updated_by_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("max_results >= 1 AND max_results <= 50", name="ck_web_space_max_results"),
        sa.CheckConstraint("max_fetch_bytes > 0", name="ck_web_space_fetch_bytes"),
        sa.CheckConstraint("max_requests_per_minute > 0", name="ck_web_space_rate"),
    )
    op.create_table(
        "web_approved_urls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("used_at IS NULL OR used_at >= created_at", name="ck_web_token_used_after_create"),
    )
    op.create_index(
        "ix_web_approved_urls_scope",
        "web_approved_urls",
        ["space_id", "account_id", "expires_at"],
    )
    op.create_table(
        "web_request_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("tool", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("query_hash", sa.String(128), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("policy_decision", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "tool IN ('search_web','fetch_approved_page')", name="ck_web_usage_tool"
        ),
    )
    op.create_index(
        "ix_web_usage_scope_time", "web_request_usage", ["space_id", "account_id", "created_at"]
    )
    op.create_table(
        "web_citations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("trust", sa.String(16), nullable=False, server_default="external"),
        sa.CheckConstraint("trust = 'external'", name="ck_web_citation_external_trust"),
    )
    op.create_index("ix_web_citations_run", "web_citations", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_web_citations_run", table_name="web_citations")
    op.drop_table("web_citations")
    op.drop_index("ix_web_usage_scope_time", table_name="web_request_usage")
    op.drop_table("web_request_usage")
    op.drop_index("ix_web_approved_urls_scope", table_name="web_approved_urls")
    op.drop_table("web_approved_urls")
    op.drop_table("web_space_configs")
    op.drop_table("web_platform_configs")
