"""Steward 模型辅助层（09-06 子任务 B；候选/排序/解释，child run 审计）。

三部分（design §1）：
1. agent_space_provider_settings 加 assist_candidate/assist_ranking/assist_explanation
   三列（空间级开关；纯加列可直接 add_column，server_default=false 保证存量行为
   与确定性基线逐字节等价）。
2. 新表 steward_model_calls：child run 审计（09-01 决策"另立任务"兑现）——
   Steward 每次模型调用一行，prompt 只存 sha256 摘要与长度，永不存明文；
   (job_id, assist_kind, seq) 唯一支撑 job 重试幂等（不重复花费 token）。
3. 新表 steward_llm_candidates：LLM 关系候选内部池；(space_id, candidate_digest)
   唯一跨 job 去重。红线：候选不经过确定性矩阵绝不进卡片/任何正式写入。
4. action_cards 加 reason_text_llm / presentation_rank 两列（解释与排序产物；
   均可空——NULL 即回退模板文案 / created_at 既有序）。

downgrade：逐项 drop（纯 schema 回退，无不可逆数据风险）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_steward_model_assist"
down_revision: str | None = "0033_agent_space_provider_settings_agent_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("assist_candidate", "assist_ranking", "assist_explanation"):
        op.add_column(
            "agent_space_provider_settings",
            sa.Column(column, sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    op.add_column("action_cards", sa.Column("reason_text_llm", sa.Text(), nullable=True))
    op.add_column("action_cards", sa.Column("presentation_rank", sa.Integer(), nullable=True))

    op.create_table(
        "steward_model_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("steward_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column(
            "assist_kind",
            sa.String(16),
            sa.CheckConstraint(
                "assist_kind IN ('candidate','ranking','explanation')",
                name="ck_smc_assist_kind",
            ),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            sa.Integer(),
            sa.ForeignKey("agent_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt_digest", sa.String(64), nullable=False),
        sa.Column("prompt_chars", sa.Integer(), nullable=False),
        sa.Column("completion_chars", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('succeeded','failed','degraded','skipped')", name="ck_smc_status"
            ),
            server_default="succeeded",
            nullable=False,
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("seq", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "assist_kind", "seq", name="uq_smc_job_kind_seq"),
    )
    op.create_table(
        "steward_llm_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Integer(),
            sa.ForeignKey("family_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("steward_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_kind", sa.String(48), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("candidate_digest", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint("status IN ('proposed','dismissed')", name="ck_slc_status"),
            server_default="proposed",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("space_id", "candidate_digest", name="uq_slc_space_digest"),
    )


def downgrade() -> None:
    op.drop_table("steward_llm_candidates")
    op.drop_table("steward_model_calls")
    op.drop_column("action_cards", "presentation_rank")
    op.drop_column("action_cards", "reason_text_llm")
    for column in ("assist_candidate", "assist_ranking", "assist_explanation"):
        op.drop_column("agent_space_provider_settings", column)
