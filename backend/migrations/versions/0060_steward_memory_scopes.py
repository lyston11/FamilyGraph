"""Steward 可读记忆级别（平台级 + 空间级）。

## 为什么需要

`memory_rag._ELIGIBILITY_SQL` 用 `:is_assistant = 1` 把 private/public 分支对 steward
恒判假，使 steward 读不到任何记忆。本迁移只加**配置列**，让「管家可以读哪些级别的记忆」
成为可治理的部署参数；它本身不改变任何检索行为——两列默认空串，等于空集，
而空集下 steward 的可读集仍为空，与迁移前逐字等价。

## 为什么是一列而不是三个布尔列

「允许哪些级别」在语义上是一个**集合**（值域 `MEMORY_SCOPES`），有效值取平台与空间的
**交集**。交集是集合运算，不是布尔与；写成三个布尔列还要再定一套"三列怎么相与"的规则，
且未来新增 scope 必须再加列。这里用单列存规范化编码（按 `MEMORY_SCOPES` 声明顺序去重、
逗号分隔），值域校验在服务层，因此不加 CHECK——加 CHECK 在 SQLite 上对已有表需要
`batch_alter_table` 整表重建，而本表被 RAG eligibility 子查询引用，重建风险大于收益。

## 降级语义（不是数据回退，是配置丢失）

降级 drop 两列，已生效的配置随之丢失、回落为默认空集，即「管家读不到记忆」。
这是**安全方向**（fail-closed），与 0059 拒绝降级的情形相反：那里丢掉取代指针会让
已取代的旧事实重新进入检索，是静默的**事实回退**。这里丢掉的只是"允许读"的许可，
不会让任何不可见内容变得可见，因此不设 refusal guard；但降级确实会静默关掉一个
已配置的能力，重新升级后配置为空，必须重新设置。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations._preflight import run_ancestor_preflight

revision = "0060_steward_memory_scopes"
down_revision = "0059_memory_supersede"
branch_labels = None
depends_on = None

TABLES = ("platform_feature_configs", "agent_space_provider_settings")
COLUMN = "steward_memory_scopes"


def _existing_columns(table: str) -> set[str]:
    from sqlalchemy import inspect

    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # 幂等：中断后重跑不得报错（与 0056/0059 同一约定）。
    for table in TABLES:
        if COLUMN not in _existing_columns(table):
            op.add_column(
                table,
                sa.Column(COLUMN, sa.String(length=64), nullable=False, server_default=""),
            )


def downgrade() -> None:
    # 祖先拒绝合同必须先于本迁移的任何 DDL（SQLite 的 DDL 不保证事务回滚，
    # 否则会留下半降级 schema）。理由与实现见 migrations/_preflight.py。
    run_ancestor_preflight(op.get_bind(), op.get_context(), down_revision=down_revision)

    for table in reversed(TABLES):
        if COLUMN in _existing_columns(table):
            op.drop_column(table, COLUMN)
