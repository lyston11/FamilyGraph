"""agent_runtime：Agent Session/Message/Run/Event/Job 与 Provider 配置表。

变更总览（notes.md「统一执行模型裁定」）：
- agent_sessions：scope 三元组（account_id/space_id/agent_kind）创建后不可变，
  以 BEFORE UPDATE trigger 数据库级强制；服务层无更新路径。
- agent_messages：role CHECK；(session_id, idempotency_key) 部分唯一（key 非空时）。
- agent_runs：唯一执行记录，FSM 列 queued|leased|running|succeeded|failed|
  cancelled|expired；partial unique 保证每 session 至多一个 active run。
- agent_jobs：durable queue 条目与 run 1:1。建表顺序处理循环外键：
  先建 jobs（无 run_id），再建 runs（job_id SET NULL 可空 FK），最后 batch
  重建 jobs 补 run_id NOT NULL UNIQUE FK——新表无数据，重建零成本。
- agent_providers / agent_space_provider_settings：Provider 注册（密钥只存密文）
  与空间级选择开关；policy 结果由服务层推导，不落库。

说明：SQLite 迁移按非事务 DDL 处理；downgrade 仅结构还原。

Revision ID: 0009_agent_runtime
Revises: 0008_v2_foundation
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_agent_runtime"
down_revision: str | None = "0008_v2_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUN_STATUS_CHECK = (
    "status IN ('queued','leased','running','succeeded','failed','cancelled','expired')"
)
_KIND_CHECK = "kind IN ('assistant','steward')"

_SCOPE_TRIGGER_SQL = """
CREATE TRIGGER trg_agent_sessions_scope_immutable
BEFORE UPDATE ON agent_sessions
WHEN OLD.account_id <> NEW.account_id
  OR OLD.space_id <> NEW.space_id
  OR OLD.agent_kind <> NEW.agent_kind
BEGIN
    SELECT RAISE(ABORT, 'agent_sessions scope is immutable');
END;
"""


def upgrade() -> None:
    # ---- 1. 会话（scope 不可变）----
    op.create_table(
        "agent_sessions",
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
            "agent_kind",
            sa.String(16),
            sa.CheckConstraint(
                "agent_kind IN ('assistant','steward')", name="ck_agent_sessions_kind"
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.execute(sa.text(_SCOPE_TRIGGER_SQL))

    # ---- 2. 消息 ----
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(16),
            sa.CheckConstraint(
                "role IN ('user','assistant','system')", name="ck_agent_messages_role"
            ),
            nullable=False,
        ),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_messages_session_id", "agent_messages", ["session_id"])
    op.create_index(
        "uq_agent_messages_session_key",
        "agent_messages",
        ["session_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # ---- 3. durable queue 条目（先无 run_id，步骤 5 补齐 FK）----
    op.create_table(
        "agent_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "kind",
            sa.String(16),
            sa.CheckConstraint(_KIND_CHECK, name="ck_agent_jobs_kind"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(_RUN_STATUS_CHECK, name="ck_agent_jobs_status"),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("leased_by", sa.String(120), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_jobs_lease_scan", "agent_jobs", ["kind", "status"])
    op.create_index(
        "uq_agent_jobs_space_active",
        "agent_jobs",
        ["space_id"],
        unique=True,
        sqlite_where=sa.text("kind = 'steward' AND status IN ('queued','leased','running')"),
    )

    # ---- 4. 执行记录（每 session 至多一个 active run）----
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("agent_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("agent_jobs.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "kind",
            sa.String(16),
            sa.CheckConstraint(_KIND_CHECK, name="ck_agent_runs_kind"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(_RUN_STATUS_CHECK, name="ck_agent_runs_status"),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("tool_allowlist_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])
    op.create_index(
        "uq_agent_runs_session_active",
        "agent_runs",
        ["session_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued','leased','running')"),
    )

    # ---- 5. 公开事件流 ----
    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("public_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_agent_run_events_run_seq", "agent_run_events", ["run_id", "seq"], unique=True
    )

    # ---- 6. jobs.run_id NOT NULL UNIQUE FK：batch 重建空表补齐循环外键另一半 ----
    with op.batch_alter_table("agent_jobs") as batch:
        batch.add_column(
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            )
        )

    # ---- 7. Provider 配置 ----
    op.create_table(
        "agent_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "kind",
            sa.String(32),
            sa.CheckConstraint(
                "kind IN ('openai_compatible','local')", name="ck_agent_providers_kind"
            ),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("allowed_models_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "agent_space_provider_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "provider_id",
            sa.Integer(),
            sa.ForeignKey("agent_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("cloud_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("local_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_agent_sessions_scope_immutable"))
    op.drop_table("agent_space_provider_settings")
    op.drop_table("agent_providers")
    op.drop_table("agent_run_events")
    op.drop_table("agent_runs")
    op.drop_table("agent_jobs")
    op.drop_table("agent_messages")
    op.drop_table("agent_sessions")
