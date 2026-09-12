"""Steward 辅助批次/attempt 结构（09-11）：事务隔离 + 预算预留 + 崩溃恢复。

三部分（跟随 0036_steward_production_scheduling）：
1. 新表 steward_assist_batches：每 job 至多一行（UNIQUE job_id）的辅助子阶段
   状态行。core 短事务提交确定性结果时在同一事务登记；HTTP 只发生在批次
   lease 之后、任何业务写事务之外（R1）。fence_json 存注册时证据快照，发送
   前与写回前各重验一次（R4）。
2. steward_model_calls 扩展为 attempt 审计：新列 batch_id（FK，CASCADE）、
   subject_key / input_hash / attempt_no（subject 维度 attempt 键，唯一索引
   uq_smc_attempt_key）、reserved_input_tokens / reserved_output_tokens（发送
   前预算预留）、billed_tokens（保守计费结算：usage 缺失/负数/部分字段回落
   预留）、response_bytes、output_json（解析验证后的可写回产物，非原始
   payload；崩溃后凭此恢复写回）。
3. ck_smc_status CHECK 扩展：新增 reserved / in_flight / unknown 状态
   （SQLite 用 batch_alter_table 重建表替换约束）。

崩溃合同（替代旧 design 的"SAVEPOINT 防回滚"说法）：四个崩溃点——core 提交
后（批次与 core 同事务，原子）、发送前（attempt reserved 可恢复）、发送后
审计前（in_flight + lease 过期 → unknown，保守计费且不自动重发）、写回前
（succeeded + output_json → 重跑 fence 后 CAS 写回）。均不产生 Assistant
三表行。downgrade：drop 新列/新表/新索引/换回旧 CHECK（停止辅助执行者并
确认无活跃 lease 后执行）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_steward_assist_batches"
down_revision: str | None = "0036_steward_production_scheduling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUS_CHECK = "status IN ('succeeded','failed','degraded','skipped')"
_NEW_STATUS_CHECK = (
    "status IN ('succeeded','failed','degraded','skipped','reserved','in_flight','unknown')"
)


def upgrade() -> None:
    op.create_table(
        "steward_assist_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("space_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("fence_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','leased','applying','applied','failed','superseded')",
            name="ck_sab_status",
        ),
        sa.UniqueConstraint("job_id", name="uq_sab_job"),
        sa.ForeignKeyConstraint(
            ["space_id"], ["family_spaces.id"], name="fk_sab_space", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["steward_jobs.id"], name="fk_sab_job", ondelete="CASCADE"
        ),
        sa.Index("ix_steward_assist_batches_due", "status", "next_attempt_at"),
    )
    op.add_column("steward_model_calls", sa.Column("batch_id", sa.Integer(), nullable=True))
    op.add_column("steward_model_calls", sa.Column("subject_key", sa.String(200), nullable=True))
    op.add_column("steward_model_calls", sa.Column("input_hash", sa.String(64), nullable=True))
    op.add_column(
        "steward_model_calls",
        sa.Column("attempt_no", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "steward_model_calls", sa.Column("reserved_input_tokens", sa.Integer(), nullable=True)
    )
    op.add_column(
        "steward_model_calls", sa.Column("reserved_output_tokens", sa.Integer(), nullable=True)
    )
    op.add_column("steward_model_calls", sa.Column("billed_tokens", sa.Integer(), nullable=True))
    op.add_column("steward_model_calls", sa.Column("response_bytes", sa.Integer(), nullable=True))
    op.add_column("steward_model_calls", sa.Column("output_json", sa.JSON(), nullable=True))
    with op.batch_alter_table("steward_model_calls") as batch_op:
        batch_op.create_foreign_key(
            "fk_smc_batch", "steward_assist_batches", ["batch_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.drop_constraint("ck_smc_status", type_="check")
        batch_op.create_check_constraint("ck_smc_status", _NEW_STATUS_CHECK)
    op.create_index(
        "uq_smc_attempt_key",
        "steward_model_calls",
        ["job_id", "assist_kind", "subject_key", "input_hash", "attempt_no"],
        unique=True,
    )


def downgrade() -> None:
    # 新状态（reserved/in_flight/unknown）不在旧 CHECK 集合内：先把它们收敛为
    # failed（保留行与已计费记录，不删历史计费），再换 CHECK。回滚是应急路径；
    # 中间态行本就不可信（崩溃恢复语义），failed + 降级标记是保守终态。
    op.execute(
        "UPDATE steward_model_calls SET status = 'failed', error_code = 'downgrade_forced' "
        "WHERE status IN ('reserved', 'in_flight', 'unknown')"
    )
    with op.batch_alter_table("steward_model_calls") as batch_op:
        batch_op.drop_constraint("fk_smc_batch", type_="foreignkey")
        batch_op.drop_constraint("ck_smc_status", type_="check")
        batch_op.create_check_constraint("ck_smc_status", _OLD_STATUS_CHECK)
    op.drop_index("uq_smc_attempt_key", table_name="steward_model_calls")
    op.drop_column("steward_model_calls", "output_json")
    op.drop_column("steward_model_calls", "response_bytes")
    op.drop_column("steward_model_calls", "billed_tokens")
    op.drop_column("steward_model_calls", "reserved_output_tokens")
    op.drop_column("steward_model_calls", "reserved_input_tokens")
    op.drop_column("steward_model_calls", "attempt_no")
    op.drop_column("steward_model_calls", "input_hash")
    op.drop_column("steward_model_calls", "subject_key")
    op.drop_column("steward_model_calls", "batch_id")
    op.drop_table("steward_assist_batches")
