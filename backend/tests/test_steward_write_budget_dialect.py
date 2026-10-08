"""`writer_turn` 的方言契约：PostgreSQL 上必须是 no-op。

## 为什么这是安全要求（实测生产死锁）

本预算的存在理由是 SQLite 的 busy handler 轮询与单写者模型。在 PostgreSQL 上
保留它会形成**PostgreSQL 看不到的跨资源死锁**：

```text
scan_due_spaces   持 DB 计数行锁（经 _immediate_tx，不取 writer_turn）
                  → 等 DB space 计数行锁
steward-core_*    持 DB 计数行锁（write_transaction 已取 writer_turn）
                  → 等 Python writer_turn 锁
```

`scan_due_spaces` 与协调器的锁序相反，而 PG 的死锁检测只看数据库锁，看不到
`writer_turn`，因此这个环**永不超时**。

实测（生产 2026-10-08）：4 个 steward 作业冻结 3.5 小时，4 条连接停在
`idle in transaction` 持有计数行锁，7 个线程阻塞在 `writer_turn` 上，
`reaper_pass` 同样卡住——整套 steward 永久停摆。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from app.services import steward_write_budget


def _engine(dialect: str):
    eng = MagicMock()
    eng.dialect.name = dialect
    return eng


def test_postgresql_does_not_block_on_the_write_budget():
    """PostgreSQL 上并发进入 `writer_turn` 不得互相阻塞。

    SQLite 上同一段代码会串行（这是它的设计目的）；PG 上必须直接放行，
    否则就会与持有计数行锁的调用方形成环。
    """
    engine = _engine("postgresql")
    entered = threading.Barrier(3, timeout=5)
    done: list[int] = []

    def worker() -> None:
        with steward_write_budget.writer_turn(engine):
            entered.wait()
            done.append(1)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(done) == 3, (
        f"只有 {len(done)}/3 个线程进入——PG 上 writer_turn 仍在互相阻塞，"
        "会与计数行锁形成 PG 看不到的死锁"
    )


def test_postgresql_does_not_register_a_budget():
    """PG 上不得注册预算对象（no-op 的直接证据）。"""
    engine = _engine("postgresql")
    with steward_write_budget.writer_turn(engine):
        pass
    assert engine not in steward_write_budget._budgets, "PG 上注册了预算对象——说明没有走 no-op 分支"


def test_sqlite_still_serialises():
    """SQLite 行为必须保持不变：同引擎的并发进入仍串行。"""
    engine = _engine("sqlite")
    order: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        with steward_write_budget.writer_turn(engine):
            with lock:
                order.append(f"in-{name}")
            time.sleep(0.05)
            with lock:
                order.append(f"out-{name}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    # 串行时必然成对出现：in-X, out-X, in-Y, out-Y
    assert order[0].startswith("in-") and order[1].startswith("out-"), f"SQLite 上未串行：{order}"
