"""Steward 生产调度（09-11）：空间周期扫描表 + StewardJob 调度元数据列。

三部分：
1. 新表 steward_space_schedules：每空间一行扫描调度（next_scan_at 到期时间、
   last_scheduled_cursor 最近扫描水位、policy_version 追补口径）。扫描 tick
   用短 BEGIN IMMEDIATE 选择到期空间，经 canonical enqueue 合同登记 core job；
   空间删除 CASCADE 清除。next_scan_at 索引支撑到期选择。
2. steward_jobs 加 available_at（可重试失败的有限退避；NULL=立即可租）。
3. steward_jobs 加 retry_of_job_id（人工重跑新作业逻辑引用被关联历史作业；
   终态不复活，只建新行——不设 FK，与 executed_event_id/superseded_by_id 同
   哲学：历史作业行只增不删，避免空间级联下的自引用环）与 error_code（安全
   错误分类码；异常原文/SQL 参数永不落库，见 findings F16）。

均为纯加列/加表，无存量数据转换，行为零变化（新列可空、新表初始为空）。
downgrade：逐项 drop（停止 worker 后回滚新增调度字段）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_steward_production_scheduling"
down_revision: str | None = "0035_space_lineage_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "steward_space_schedules",
        sa.Column("space_id", sa.Integer(), primary_key=True),
        sa.Column("next_scan_at", sa.DateTime(), nullable=False),
        sa.Column("last_scheduled_cursor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id"], ["family_spaces.id"], name="fk_sss_space", ondelete="CASCADE"
        ),
        sa.Index("ix_steward_space_schedules_due", "next_scan_at"),
    )
    op.add_column("steward_jobs", sa.Column("available_at", sa.DateTime(), nullable=True))
    op.add_column("steward_jobs", sa.Column("retry_of_job_id", sa.Integer(), nullable=True))
    op.add_column("steward_jobs", sa.Column("error_code", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("steward_jobs", "error_code")
    op.drop_column("steward_jobs", "retry_of_job_id")
    op.drop_column("steward_jobs", "available_at")
    op.drop_table("steward_space_schedules")
