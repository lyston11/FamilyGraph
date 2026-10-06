"""writer epoch 与 migration health 的持久状态（C7）。

## 为什么需要

切换 SQLite → PostgreSQL 的最大风险是**双主**：两个 writer 同时裁决 lease/settle/
counter，产生两个真相，且无法事后对账修复（两边都可能已对外产生结果）。

本迁移建立**单行**状态表：

```text
writer_state(id=1, stage, epoch, updated_at, updated_by)
```

- `stage`：`sqlite` / `shadow` / `pg_control` / `pg_all`，只能逐级移动；
- `epoch`：单调递增。每次阶段变化 +1，使**所有**旧实例立刻失去写权，
  不需要逐实例重启。

## 为什么是单行而不是配置

配置（env）无法在运行时安全变更：改 env 需要逐实例重启，重启期间新旧实例并存，
正是双主窗口。把状态放进数据库后，一次 UPDATE 就完成了「切换 + 让旧实例失效」。

## 为什么初始不插入行

与 `platform_feature_configs` 同一约定：迁移只建表，不插默认行。无行时
`read_state` 回落到 `FG_WRITER_STAGE`（默认 `sqlite`），使迁移**之前**的行为
与迁移之后一致——迁移本身不改变任何路由。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations._preflight import run_ancestor_preflight

revision = "0058_writer_state"
down_revision = "0057_steward_capacity_release_gate"
branch_labels = None
depends_on = None

TABLE = "writer_state"


def _table_exists() -> bool:
    from sqlalchemy import inspect

    return TABLE in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _table_exists():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        # 阶段枚举由 CHECK 兜底：写错阶段名会让 health 与路由判断全部失准。
        sa.CheckConstraint(
            "stage IN ('sqlite','shadow','pg_control','pg_all')",
            name="ck_writer_state_stage",
        ),
        # epoch 不得为负：负值会让「递增后仍小于旧值」的实例误判自己仍是 writer。
        sa.CheckConstraint("epoch >= 0", name="ck_writer_state_epoch_non_negative"),
        # 单例：id 只能是 1，避免出现两行状态（那就是两个真相）。
        sa.CheckConstraint("id = 1", name="ck_writer_state_singleton"),
    )


def downgrade() -> None:
    if not _table_exists():
        return
    connection = op.get_bind()
    context = op.get_context()
    # 祖先拒绝必须先于本迁移的任何 DDL（SQLite DDL 不保证事务回滚）。
    run_ancestor_preflight(connection, context, down_revision=down_revision)
    # refusal guard：已切到 PostgreSQL 后 DROP 状态表会让「谁是真源」不可知，
    # 从而无法安全回滚（回滚需要 epoch 来让旧实例失效）。
    stage = connection.execute(sa.text("SELECT stage FROM writer_state WHERE id = 1")).scalar()
    if stage is not None and stage != "sqlite":
        raise RuntimeError(
            f"refusal: writer stage is {stage!r}; roll it back to 'sqlite' "
            "before dropping writer_state, or the routing truth is lost"
        )
    op.drop_table(TABLE)
