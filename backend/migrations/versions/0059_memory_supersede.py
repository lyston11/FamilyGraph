"""记忆的时间有效区间与取代指针（P1）。

## 为什么需要

在此之前 `memories` 只有 `retention_until`（到期），没有「被取代」。同一个人的
职业、住址、称呼变化后，新旧两条都是 `status='active'`，检索会同时命中，模型看到
**互相矛盾的事实**且无法判断哪个有效——这比「检索不到」更有害。

本迁移只加列，不改任何现有行的语义：六列全部可空，`superseded_by_id IS NULL` 是
「未被取代」，与迁移前所有行的行为完全一致。因此迁移本身**不改变检索结果**。

## 为什么不用 CHECK 约束

判据（不自我指向、`supersede_reason` 枚举、`valid_to >= valid_from`）都由服务层在
同一事务内校验。SQLite 上给已有表新增 CHECK 需要 `batch_alter_table` 整表重建
（复制全表），在本表还会被 RAG eligibility 子查询引用的情况下，重建风险明显大于
一个可由服务层断言覆盖的约束。

## 为什么保留 `superseded_at` / `restored_at`

取代是可撤销的（用户改主意）。只保留「当前是否被取代」无法审计「什么时候被取代、
什么时候被恢复」，而记忆层的每一次可见性变化都必须可解释。

## refusal guard

降级会丢掉取代指针，使已取代的旧事实**重新进入检索**——那是静默的事实回退，不是
schema 回退。因此只要存在任何 `superseded_by_id IS NOT NULL` 的行就拒绝降级，必须
先由人工显式恢复这些记忆。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations._preflight import run_ancestor_preflight

revision = "0059_memory_supersede"
down_revision = "0058_writer_state"
branch_labels = None
depends_on = None

TABLE = "memories"
INDEX = "ix_memories_supersede"

COLUMNS = (
    sa.Column("valid_from", sa.DateTime(), nullable=True),
    sa.Column("valid_to", sa.DateTime(), nullable=True),
    sa.Column("superseded_by_id", sa.Integer(), nullable=True),
    sa.Column("supersede_reason", sa.String(length=16), nullable=True),
    sa.Column("superseded_at", sa.DateTime(), nullable=True),
    sa.Column("restored_at", sa.DateTime(), nullable=True),
)


def _existing_columns() -> set[str]:
    from sqlalchemy import inspect

    return {column["name"] for column in inspect(op.get_bind()).get_columns(TABLE)}


def _index_exists() -> bool:
    from sqlalchemy import inspect

    return INDEX in {item["name"] for item in inspect(op.get_bind()).get_indexes(TABLE)}


def upgrade() -> None:
    # 幂等：中断后重跑不得报错（与 0056 同一约定）。
    present = _existing_columns()
    for column in COLUMNS:
        if column.name not in present:
            op.add_column(TABLE, column)
    if not _index_exists():
        # 恢复/审计按「谁取代了它」查行；检索侧的 `superseded_by_id IS NULL`
        # 由 memories 主键子查询覆盖，不需要额外索引。
        op.create_index(INDEX, TABLE, ["superseded_by_id"])


def downgrade() -> None:
    # 祖先拒绝合同必须先于本迁移的任何 DDL（SQLite 的 DDL 不保证事务回滚，
    # 否则会留下半降级 schema）。理由与实现见 migrations/_preflight.py。
    run_ancestor_preflight(op.get_bind(), op.get_context(), down_revision=down_revision)

    connection = op.get_bind()
    superseded = connection.execute(
        sa.text(f"SELECT count(*) FROM {TABLE} WHERE superseded_by_id IS NOT NULL")
    ).scalar()
    if superseded:
        raise RuntimeError(
            f"refusal: {superseded} memory row(s) carry a supersede pointer; "
            "dropping the column would silently return superseded facts to "
            "retrieval — restore them first"
        )
    if _index_exists():
        op.drop_index(INDEX, table_name=TABLE)
    present = _existing_columns()
    for column in reversed(COLUMNS):
        if column.name in present:
            op.drop_column(TABLE, column.name)
