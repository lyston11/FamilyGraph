"""steward_model_calls 的容量占用/归还门（C2）。

## 为什么需要持久化门

counter 的归还必须**恰好一次**。四个归还点分散在不同函数（写回栅栏、失败结算、
租约过期恢复、崩溃点④退休），靠「每个调用点都记得归还」不可证明——漏一个就永久
泄漏名额，重复一个就凭空多出名额（`CHECK` 会拒绝，于是变成运行期报错）。

因此把门放到**行上**：

```text
capacity_acquired_at  IS NOT NULL   -> 该 attempt 曾占用名额
capacity_released_at  IS NULL       -> 尚未归还
```

归还 = 「acquired 非空且 released 为空」时递减并写 released_at。这个条件对**同一行**
只能成立一次，且崩溃后重跑仍成立（已写入的 released_at 会阻止第二次递减）。

## 为什么不用「status 从 in_flight 离开」当门

status 是可变的业务状态，会被其他路径改写（例如 recovery 把 in_flight 改成 unknown、
再由栅栏改成 skipped）。用 status 推断「是否已归还」会在多次状态变更后失去依据。
专用时间戳列是单调的，不受业务状态机影响。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations._preflight import run_ancestor_preflight

revision = "0057_steward_capacity_release_gate"
down_revision = "0056_agent_capacity_counters"
branch_labels = None
depends_on = None

# 两张配额承载表的门列：assist attempt 与 steward job。
# 两张表都需要同样的「恰好一次」保证，因此用同一对列名，便于统一审计。
TABLES = ("steward_model_calls", "steward_jobs")


def _has_column(table: str, name: str) -> bool:
    from sqlalchemy import inspect

    inspector = inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return name in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    # 幂等：重复执行不得报错（迁移可能在中断后重跑）
    for table in TABLES:
        if _has_column(table, "capacity_acquired_at"):
            continue
        op.add_column(
            table,
            sa.Column("capacity_acquired_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("capacity_released_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    # refusal guard：仍有未归还的占用说明在途租约存在，DROP 列会丢失「欠一次归还」
    # 的证据，使名额永久泄漏且无法事后对账。必须先 settle/recover 再降级。
    present = [t for t in TABLES if _has_column(t, "capacity_acquired_at")]
    if not present:
        return
    connection = op.get_bind()
    context = op.get_context()
    # 祖先拒绝必须先于本迁移的任何 DDL（SQLite DDL 不保证事务回滚）。
    run_ancestor_preflight(connection, context, down_revision=down_revision)
    for table in present:
        outstanding = connection.execute(
            sa.text(
                f"SELECT count(*) FROM {table}"
                " WHERE capacity_acquired_at IS NOT NULL AND capacity_released_at IS NULL"
            )
        ).scalar()
        if outstanding:
            raise RuntimeError(
                f"refusal: {outstanding} row(s) in {table} still hold capacity; "
                "settle or recover them before downgrading"
            )
    for table in present:
        op.drop_column(table, "capacity_released_at")
        op.drop_column(table, "capacity_acquired_at")
