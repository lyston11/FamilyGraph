"""PostgreSQL 专属回归：行锁在多连接并发下必须真正串行化配额。

## 为什么需要单独一个文件

后端套件**全部跑在 SQLite** 上（`conftest` 设 `DATA_DIR`、不设 `DATABASE_URL`），
而 `capacity._lock` 的行为按方言分派：

```python
if db.bind.dialect.name == "postgresql":
    ... .with_for_update()      # PostgreSQL：必须显式行锁
return db.get(AgentCapacityCounter, key)   # SQLite：BEGIN IMMEDIATE 已取全库写锁
```

**实测（2026-10-23）**：删掉 `.with_for_update()` 后，
`test_capacity_counters` / `test_capacity_bootstrap` / `test_agent_execution_admission` /
`test_stream_capacity` / `test_cluster_capacity_admission` 共 **53 项测试全部通过**。
也就是说，**多实例配额正确性的核心机制在 SQLite 套件里没有任何回归**——
SQLite 的 `BEGIN IMMEDIATE` 把这个问题掩盖掉了。

## 本文件怎么补

`PGTEST_DSN` 存在时，用**两个真实连接**（独立 Session、独立事务）验证：

1. 第二个连接在第一个持有行锁时**阻塞**（而不是读到陈旧值后各自 +1）；
2. 并发 `acquire` 不超额。

`PGTEST_DSN` 缺省时整个文件 skip——CI 默认不跑，专用验收实例上跑。

**必须用隔离数据库**：本文件会读写 `agent_capacity_counters`。
"""

from __future__ import annotations

import os
import threading
import time

import pytest

DSN = os.environ.get("PGTEST_DSN", "")

pytestmark = pytest.mark.skipif(
    not DSN or "sqlite" in DSN,
    reason="需要 PGTEST_DSN 指向隔离 PostgreSQL（SQLite 的 BEGIN IMMEDIATE 会掩盖行锁问题）",
)

#: 专用 scope_id 区间，避免与真实租户冲突。
SCOPE_BASE = int(os.environ.get("PGTEST_SCOPE_BASE", "970000"))


def _engine():
    from sqlalchemy import create_engine

    return create_engine(DSN, pool_size=4, max_overflow=4)


def _spec(scope_id: int):
    from app.services import capacity

    return capacity.CounterSpec("account", scope_id, capacity.RESOURCE_ASSISTANT_RUN)


def _seed(engine, scope_id: int, capacity_value: int) -> None:
    from sqlalchemy.orm import Session

    from app.services import capacity

    with Session(engine) as db:
        with db.begin():
            capacity.ensure_counter(db, _spec(scope_id), capacity=capacity_value)


def _reset(engine, scope_id: int) -> None:
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.models.agent import AgentCapacityCounter

    with Session(engine) as db:
        with db.begin():
            db.execute(
                delete(AgentCapacityCounter).where(
                    AgentCapacityCounter.scope_id == scope_id,
                    AgentCapacityCounter.resource_kind == "assistant_run",
                )
            )


def test_second_connection_blocks_on_the_row_lock():
    """第一个连接持有行锁时，第二个连接的 `acquire` 必须**阻塞**。

    这是行锁**唯一**的可观测语义：没有它，第二个连接会读到陈旧的 `active`，
    各自 +1，于是「上限 2」被放成 4（`cross_instance_capacity.py` 实测过该越限）。

    判据用**耗时**而非结果：阻塞意味着第二个 `acquire` 直到第一个提交才返回。
    若行锁缺失，它会立刻返回。
    """
    from sqlalchemy.orm import Session

    from app.services import capacity

    engine = _engine()
    scope_id = SCOPE_BASE
    _reset(engine, scope_id)
    _seed(engine, scope_id, 5)

    holder_ready = threading.Event()
    release_holder = threading.Event()
    elapsed: list[float] = []

    def holder() -> None:
        with Session(engine) as db:
            with db.begin():
                assert capacity.acquire(db, [_spec(scope_id)])
                holder_ready.set()
                release_holder.wait(timeout=30)

    def waiter() -> None:
        holder_ready.wait(timeout=30)
        time.sleep(0.3)  # 确保 holder 已取到锁
        started = time.perf_counter()
        with Session(engine) as db:
            with db.begin():
                capacity.acquire(db, [_spec(scope_id)])
        elapsed.append(time.perf_counter() - started)

    h = threading.Thread(target=holder, daemon=True)
    w = threading.Thread(target=waiter, daemon=True)
    h.start()
    w.start()
    holder_ready.wait(timeout=10)
    time.sleep(1.0)
    blocked_so_far = not elapsed, "第二个 acquire 未阻塞——行锁未生效"
    release_holder.set()
    h.join(timeout=30)
    w.join(timeout=30)

    assert elapsed, "waiter 未完成"
    assert elapsed[0] >= 0.5, (
        f"第二个 acquire 仅耗时 {elapsed[0]:.3f}s，未等待 holder 释放行锁；"
        "说明 `.with_for_update()` 缺失或未生效"
    )
    assert blocked_so_far[0], blocked_so_far[1]

    _reset(engine, scope_id)
    engine.dispose()


def test_concurrent_acquire_never_exceeds_capacity():
    """多线程并发 `acquire` 不得超额——行锁的端到端判据。"""
    from sqlalchemy.orm import Session

    from app.services import capacity

    engine = _engine()
    scope_id = SCOPE_BASE + 1
    _reset(engine, scope_id)
    cap = 3
    _seed(engine, scope_id, cap)

    granted: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait(timeout=30)
        with Session(engine) as db:
            with db.begin():
                ok = capacity.acquire(db, [_spec(scope_id)])
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(granted) == 8, granted
    assert sum(granted) == cap, f"期望恰好 {cap} 个授予，实际 {sum(granted)}"

    from sqlalchemy import select

    from app.models.agent import AgentCapacityCounter

    with Session(engine) as db:
        active = db.scalar(
            select(AgentCapacityCounter.active).where(
                AgentCapacityCounter.scope_kind == "account",
                AgentCapacityCounter.scope_id == scope_id,
                AgentCapacityCounter.resource_kind == "assistant_run",
            )
        )
    assert active == cap, f"计数行 active={active}，期望 {cap}"

    _reset(engine, scope_id)
    engine.dispose()
