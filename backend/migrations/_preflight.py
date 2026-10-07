"""降级 preflight 的共享实现（每个会动 DDL 的新迁移都必须在 downgrade 开头调用）。

## 为什么需要

SQLite 的 DDL 不保证事务回滚。若本迁移先执行了 DDL，再由**祖先**拒绝降级，
就会留下半降级 schema（实测：`ACTUAL_ALEMBIC_DDL_COUNT=2` 且表已被 DROP）。

祖先的拒绝发生在**祖先自己开始执行时**——那已经晚于本迁移。因此本迁移必须
在动任何 DDL 之前，替祖先把它自己的拒绝条件先履行一遍。

## 两个必须做到的点（缺任一条都会失败）

1. **`iterate_revisions` 是惰性生成器**：必须显式 `list(...)` 消费才真正走位，
   否则守卫形同虚设。
2. **还要从每个 planned revision 的 `down_revision` 再走一次**：迁移自身的 preflight
   正是从**它的父 revision** 开始走位（见 0051/0053 注释），抛 `Ambiguous walk` 的
   就是那一步。只从 revision 自己走会漏掉它。

## 守卫的来源

每个守卫由**其后继**唯一镜像表达（约定见 0053/0055 注释），这里沿用同一映射：

| 祖先条件 | 镜像位置 |
|---|---|
| 0049 候选证据 | `0050._refuse_if_candidate_evidence` |
| 0051 源计时证据 | `0052._refuse_if_timing_evidence` |
| 0048 合并分叉 | `0048._preflight_parent_downgrade` |
| 0053 审批/标签证据 | 0053 内联条件（此处重述） |
"""

from __future__ import annotations

import sqlalchemy as sa


def run_ancestor_preflight(
    connection: sa.Connection, context, *, down_revision: str | None
) -> None:
    """在任何 DDL 之前履行祖先的拒绝合同。

    `down_revision` 由调用方显式传入（即本迁移的 `down_revision`）：走位必须从
    **父 revision** 开始，才是祖先 preflight 的起点。
    """
    destination = context.opts.get("destination_rev")
    if context.script is None or destination is None:
        return
    script = context.script
    start = down_revision
    if start is None or isinstance(start, tuple):
        return

    planned = {
        item.revision
        for item in script.iterate_revisions(start, destination, select_for_downgrade=True)
    }
    for revision in planned:
        # ① 生成器惰性：显式消费才真正走位
        list(script.iterate_revisions(revision, destination, select_for_downgrade=True))
        # ② 再从该 revision 的父 revision 走一次——迁移自身 preflight 的起点
        revision_obj = script.get_revision(revision)
        parent = revision_obj.down_revision if revision_obj is not None else None
        if isinstance(parent, str):
            list(script.iterate_revisions(parent, destination, select_for_downgrade=True))

    if "0049_steward_candidate_evidence" in planned or destination != start:
        guard = script.get_revision("0050_term_alias_spouse_fix")
        assert guard is not None
        guard.module._refuse_if_candidate_evidence(connection)
    if "0051_run_event_timing" in planned or destination != start:
        guard = script.get_revision("0052_seed_lineage_membership_boundary")
        assert guard is not None
        guard.module._refuse_if_timing_evidence(connection)
    if "0053_member_approval_and_labels" in planned:
        if connection.scalar(
            sa.text("SELECT 1 FROM space_member_approvals LIMIT 1")
        ) or connection.scalar(sa.text("SELECT 1 FROM member_relation_labels LIMIT 1")):
            raise RuntimeError(
                "owner-approval or relation-label evidence exists; " "retain data and roll forward"
            )
    if "0048_steward_terminology_publication" in planned:
        guard = script.get_revision("0048_steward_terminology_publication")
        assert guard is not None
        guard.module._preflight_parent_downgrade(planned=planned)
