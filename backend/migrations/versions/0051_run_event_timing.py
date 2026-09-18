"""Add sidecar-sourced timing metadata for assistant run observability.

09-17 D：`agent_run_events.created_at` 是后端入库时刻（sidecar 默认每 250ms
批量 flush），因此不能还原精确执行耗时——合成 125ms 的工具执行与其结束事件
同批入库时只相差约 1.3ms。同时 `run.started` 由 SDK `agent_start` 产生，晚于
context 获取与 session 创建，不能充当"取得执行权"时刻（也不能由心跳推算）。

本迁移只做两处**可空、向后兼容**的增列，旧行保持 NULL（历史不可还原，不倒推）：

- ``agent_run_events.timing_json``：producer 测量的有界内部计时记录（与既有
  ``context_reference_json`` 同模式），closed shape 由 pydantic 校验，永不进入
  ``public_payload``；
- ``agent_runs.first_leased_at``：本 run **首次**取得执行权的权威时刻（attempt
  0→1 时写入一次），与可续期的 ``lease_expires_at``/``heartbeat_at`` 区分。

Revision ID: 0051_run_event_timing
Revises: 0050_term_alias_spouse_fix
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_run_event_timing"
down_revision: str | None = "0050_term_alias_spouse_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_run_events",
        sa.Column("timing_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("first_leased_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # 父级拒绝合同必须先于本迁移的任何 DDL（与 0049/0050 同一约定）。
    # SQLite DDL 不保证事务回滚：若先删本迁移两列再由祖先拒绝，会留下半降级 schema
    # （实测降级到 0044 时 timing_json 已被删除）。此处从**本迁移自己的 revision**
    # 计算 planned 集，再调用祖先已有的模块级拒绝 helper，不重复实现条件。
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE agent_run_events SET id=id WHERE 0"))
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    if context.script is not None and destination is not None:
        # 走位从**父 revision** 开始（不是本迁移自己）：这正好复现父迁移自己的
        # preflight 走位。相对目标（如 `-3`）在父 revision 上是**歧义**的，Alembic
        # 会在那里抛 "Ambiguous walk"；若从本迁移自己走位，同一相对目标会被解析成
        # 另一组 revision 而不报错，于是本迁移的两列先被删掉，半降级才被父级发现。
        assert down_revision is not None
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                down_revision, destination, select_for_downgrade=True
            )
        }
        if "0049_steward_candidate_evidence" in planned:
            evidence_guard = context.script.get_revision("0050_term_alias_spouse_fix")
            assert evidence_guard is not None
            evidence_guard.module._refuse_if_candidate_evidence(connection)
        if "0048_steward_terminology_publication" in planned:
            merge_guard = context.script.get_revision("0048_steward_terminology_publication")
            assert merge_guard is not None
            merge_guard.module._preflight_parent_downgrade(planned=planned)

    # 先拒绝：一旦已有源计时/首次租赁证据，降级会永久销毁唯一的精确耗时来源。
    if (
        connection.execute(
            sa.text(
                "SELECT 1 FROM agent_run_events WHERE timing_json IS NOT NULL "
                "OR EXISTS (SELECT 1 FROM agent_runs WHERE first_leased_at IS NOT NULL) LIMIT 1"
            )
        ).first()
        is not None
    ):
        raise RuntimeError("sidecar timing evidence exists; retain data and roll forward")
    op.drop_column("agent_runs", "first_leased_at")
    op.drop_column("agent_run_events", "timing_json")
