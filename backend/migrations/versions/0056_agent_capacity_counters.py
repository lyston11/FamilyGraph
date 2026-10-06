"""agent_capacity_counters：持久化容量计数（C2）。

## 为什么需要

实测（见 `10-05` 的 `pg_control_proof.py` 与 `solution-decision.md`）：
`FOR UPDATE SKIP LOCKED` + 计数子查询在 READ COMMITTED 下**看不到并发事务尚未
提交的 in_flight 行**，每租户上限 2 被放成 5。因此配额真相必须是**持久化的计数行**，
用行锁串行化「检查并占用」。

SQLite 侧靠 `BEGIN IMMEDIATE` 的全库写锁掩盖了这个问题，所以这是纯 PostgreSQL
阻塞点，SQLite 测试无法发现。

## 设计

```text
agent_capacity_counters
  scope_kind: global | agent_kind | account | space | provider
  scope_id:   0 表示 global
  resource_kind: assistant_run | steward_job | steward_assist | tool | provider_stream
  capacity
  active
  version
  PRIMARY KEY (scope_kind, scope_id, resource_kind)
  CHECK (active >= 0 AND active <= capacity)
```

`CHECK (active <= capacity)` 是**兜底不变量**：即使应用逻辑有 bug，数据库也会拒绝
超额。它不是主要控制手段（并发下 `active` 的读取仍需行锁），而是最后一道防线。

## 锁序（冻结）

```text
global → kind → tenant → candidate
```

本迁移只建表；锁序由 `app/services/capacity.py` 在事务内保证。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_agent_capacity_counters"
down_revision = "0055_steward_assist_execution_unit"
branch_labels = None
depends_on = None

SCOPE_KINDS = ("global", "agent_kind", "account", "space", "provider")
RESOURCE_KINDS = ("assistant_run", "steward_job", "steward_assist", "tool", "provider_stream")


def _table_exists(name: str) -> bool:
    """方言中立的存在性检查。

    不能直接用 `information_schema`：它在 SQLite 上不存在（实测 `no such table:
    information_schema.tables`）。本项目的迁移必须同时在 SQLite 与 PostgreSQL 上可跑，
    因此用 SQLAlchemy inspector。
    """
    from sqlalchemy import inspect

    return name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # 幂等：重复执行不得报错（迁移可能在中断后重跑）
    if _table_exists("agent_capacity_counters"):
        return

    op.create_table(
        "agent_capacity_counters",
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resource_kind", sa.String(24), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("active", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "scope_kind",
            "scope_id",
            "resource_kind",
            name="pk_agent_capacity_counters",
        ),
        # 兜底不变量：数据库自己拒绝超额，而不是只靠应用逻辑
        sa.CheckConstraint(
            "active >= 0 AND active <= capacity",
            name="ck_acc_active_within_capacity",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('global','agent_kind','account','space','provider')",
            name="ck_acc_scope_kind",
        ),
        sa.CheckConstraint(
            "resource_kind IN ('assistant_run','steward_job','steward_assist',"
            "'tool','provider_stream')",
            name="ck_acc_resource_kind",
        ),
        sa.CheckConstraint("capacity >= 0", name="ck_acc_capacity_non_negative"),
    )
    op.create_index(
        "ix_acc_scope",
        "agent_capacity_counters",
        ["scope_kind", "scope_id"],
    )


def downgrade() -> None:
    # 祖先拒绝合同必须先于本迁移的任何 DDL（与 0051/0053/0055 同一约定）。
    #
    # SQLite 的 DDL 不保证事务回滚：若先 DROP 本迁移的表再由祖先拒绝，会留下半降级
    # schema。更关键的是**深层降级**（相对偏移或绝对目标）：本迁移上方还有多个迁移，
    # 它们各自的 preflight 会在**它们开始执行时**才抛错，而那时本迁移已经动了 DDL。
    #
    # 因此这里逐个复现同一走位，并把祖先的拒绝 helper 提前调用。每个守卫由**其后继**
    # 唯一镜像表达（0049→0050、0051→0052、0053 内联），沿用该约定而不重写判定。
    if not _table_exists("agent_capacity_counters"):
        return

    connection = op.get_bind()
    context = op.get_context()
    destination = context.opts.get("destination_rev")
    if context.script is not None and destination is not None and down_revision is not None:
        planned = {
            item.revision
            for item in context.script.iterate_revisions(
                down_revision, destination, select_for_downgrade=True
            )
        }
        # 生成器惰性：必须显式消费才会真正走位（否则守卫形同虚设）。
        #
        # 同时要从**每个 planned revision 的 down_revision** 再走一次：迁移自身的
        # preflight 正是从它的父 revision 开始走位（见 0051/0053 的注释），而那一步
        # 才是真正抛 "Ambiguous walk" 的地方。只从 revision 自己走会漏掉它，
        # 结果本迁移已 DROP 表后才由祖先报错（实测 DDL_COUNT=2）。
        for revision in planned:
            list(
                context.script.iterate_revisions(
                    revision, destination, select_for_downgrade=True
                )
            )
            revision_obj = context.script.get_revision(revision)
            parent = revision_obj.down_revision if revision_obj is not None else None
            if isinstance(parent, str):
                list(
                    context.script.iterate_revisions(
                        parent, destination, select_for_downgrade=True
                    )
                )
        if "0049_steward_candidate_evidence" in planned or destination != down_revision:
            candidate_guard = context.script.get_revision("0050_term_alias_spouse_fix")
            assert candidate_guard is not None
            candidate_guard.module._refuse_if_candidate_evidence(connection)
        if "0051_run_event_timing" in planned or destination != down_revision:
            timing_guard = context.script.get_revision("0052_seed_lineage_membership_boundary")
            assert timing_guard is not None
            timing_guard.module._refuse_if_timing_evidence(connection)
        if "0053_member_approval_and_labels" in planned:
            if connection.scalar(
                sa.text("SELECT 1 FROM space_member_approvals LIMIT 1")
            ) or connection.scalar(sa.text("SELECT 1 FROM member_relation_labels LIMIT 1")):
                raise RuntimeError(
                    "owner-approval or relation-label evidence exists; "
                    "retain data and roll forward"
                )
        if "0048_steward_terminology_publication" in planned:
            merge_guard = context.script.get_revision("0048_steward_terminology_publication")
            assert merge_guard is not None
            merge_guard.module._preflight_parent_downgrade(planned=planned)

    # 本迁移自己的 refusal guard：任何 active > 0 说明仍有在途租约，
    # 直接 DROP 会丢失容量真相。
    in_use = connection.execute(
        sa.text("SELECT count(*) FROM agent_capacity_counters WHERE active > 0")
    ).scalar()
    if in_use:
        raise RuntimeError(
            f"refusal: {in_use} counter row(s) still have active > 0; "
            "settle or recover the in-flight leases before downgrading"
        )
    op.drop_index("ix_acc_scope", table_name="agent_capacity_counters")
    op.drop_table("agent_capacity_counters")
