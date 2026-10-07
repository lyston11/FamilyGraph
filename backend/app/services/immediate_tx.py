"""方言感知的「立即事务」：把 SQLite 的 `BEGIN IMMEDIATE` 语义映射到两种数据库。

## 背景：`BEGIN IMMEDIATE` 保护的是什么

SQLite 只有单写者，`BEGIN IMMEDIATE` 在事务起点取**全库写锁**，从而覆盖
「读取 → 资格复核 → 多表写入」这类 check-then-act 窗口
（见 `spec/backend/database-guidelines.md`）。

PostgreSQL 没有全库写锁，因此**不能**逐字翻译。但它也不需要——本项目在 C2 已为
每个租户/资源维度建立**持久化 capacity counter**，并冻结了锁序：

```text
global → kind → account/space → parent/resource → run → attempt
```

counter 行锁（`SELECT ... FOR UPDATE`）在 PostgreSQL 上承担与 `BEGIN IMMEDIATE`
相同的角色：**让同一资源的并发事务串行**，且不影响其他租户。

## 因此两种方言的映射

| | SQLite | PostgreSQL |
|---|---|---|
| 取得串行化 | `BEGIN IMMEDIATE`（全库写锁） | 普通事务 + **counter 行锁**（调用方已取） |
| 隔离级别 | 单写者天然串行 | READ COMMITTED + 行锁 |
| 干净会话要求 | 必需（`in_transaction` 检查） | 同样必需 |

## 关键：PostgreSQL 分支不做额外加锁

不在这个 helper 里加 `LOCK TABLE` 或提高隔离级别，因为：

1. 那会让**全库**串行，退化掉多租户并发——正是本项目要消除的形态；
2. 真正的串行化点已在 `capacity.acquire` 的 counter 行锁上，且带冻结锁序；
3. 提高隔离级别会引入无界的 serialization failure 与重试。

因此 PostgreSQL 分支只做「干净会话检查 + 事务提交/回滚」，把串行化留给 counter。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session


def dialect_of(session: Session) -> str:
    """当前会话的方言名。`bind` 为空时按 SQLite（测试默认）。"""
    bind = session.get_bind()
    return bind.dialect.name if bind is not None else "sqlite"


@contextmanager
def immediate_tx(session: Session, *, owner: str) -> Iterator[Session]:
    """立即事务。`owner` 只用于错误信息（定位是哪个队列/服务）。

    ## 为什么 PostgreSQL 分支**不**检查 `isinstance(raw, sqlite3.Connection)`

    原实现在非 SQLite 时抛 `RuntimeError("... requires a sqlite3 connection")`。
    那是**迁移未完成**的断言，不是业务约束：它在 PostgreSQL 上会让整个 agent
    queue 与 steward 队列无法启动。现在按方言分派，SQLite 保留原有保证。
    """
    from app.services import writer_epoch

    # epoch 守卫在取写锁**之前**：epoch 过期说明本实例已不是 writer，
    # 此时连写锁都不应取——取了就说明已经开始参与写入竞争。
    writer_epoch.guard(session)
    dialect = dialect_of(session)

    if dialect == "sqlite":
        sa_conn = session.connection()
        raw = sa_conn.connection.dbapi_connection
        if not isinstance(raw, sqlite3.Connection):  # pragma: no cover - 防御
            raise RuntimeError(f"{owner} requires a sqlite3 connection")
        if raw.in_transaction:
            raise RuntimeError(f"{owner} requires a clean session without pending writes")
        sa_conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        return

    # PostgreSQL：**不加**额外锁——串行化由调用方已取的 counter 行锁承担
    # （见模块文档）。也不做「干净会话」硬检查：SQLAlchemy 的 Session 在
    # PostgreSQL 上会按需开启事务，`in_transaction()` 在正常使用中常为 True，
    # 据此报错会误伤。事务边界由本函数统一 commit/rollback 保证。
    session.connection()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
