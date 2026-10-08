"""Give online SQLite writers an opportunity between Steward write bursts.

SQLite's busy handler polls; a stream of short transactions can repeatedly
take the writer ahead of an already waiting login. All Steward coordinators
using an Engine share this FIFO budget, including delivery and collection.
Waiting happens before creating a Session, never while holding a DB lock.

## PostgreSQL：**必须**跳过本预算（否则真实死锁）

本机制的存在理由是 SQLite 的 busy handler 轮询与单写者模型；两个常量也直接
按「SQLite busy handler 的 100ms 轮询间隔」取值。PostgreSQL 有真正的行锁与
MVCC，不需要它。

更关键的是，**在 PostgreSQL 上保留它会形成 PostgreSQL 看不到的跨资源死锁**：

```text
scan_due_spaces      持 DB global 计数行锁  →  等 DB space 计数行锁
steward-core_*       持 DB space 计数行锁   →  等 Python writer_turn 锁
publish/release      持 Python writer_turn  →  等 DB global 计数行锁
```

`scan_due_spaces` 走 `_immediate_tx`（不取 `writer_turn`），而协调器走
`write_transaction`（先取 `writer_turn` 再取计数行锁），两者锁序相反。
PostgreSQL 的死锁检测只看数据库锁，**看不到 `writer_turn`**，因此这个环
永远不会被检测、也永远不会超时。

实测（生产，2026-10-08）：4 个 steward 作业冻结 3.5 小时，4 条连接停在
`idle in transaction` 持有计数行锁，7 个线程阻塞在 `writer_turn` 上，
`reaper_pass` 同样卡住而无法回收——即**整套 steward 永久停摆**。

因此 PG 分支直接放行：串行化由计数行锁与冻结锁序承担（见
`spec/backend/database-guidelines.md`）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from weakref import WeakKeyDictionary

from sqlalchemy.engine import Connection, Engine

# The default SQLite busy handler reaches 100ms polling intervals. A shared
# quiet period spans one such interval; per-coordinator sleeps would allow
# other spaces to fill the gap. These are admission budgets, not lock limits.
WRITE_BURST_SECONDS = 0.050
WRITE_QUIET_SECONDS = 0.100


class _WriteBudget:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.queue: deque[object] = deque()
        self.owner: int | None = None
        self.spent = 0.0
        self.last_finished = 0.0
        self.resume_at = 0.0

    @contextmanager
    def turn(self) -> Iterator[None]:
        ticket = object()
        identity = threading.get_ident()
        with self.condition:
            if self.owner == identity:
                raise RuntimeError("Steward write transactions cannot be nested")
            self.queue.append(ticket)
            try:
                while self.queue[0] is not ticket:
                    self.condition.wait()
                while (remaining := self.resume_at - time.monotonic()) > 0:
                    self.condition.wait(timeout=remaining)
                started = time.monotonic()
                if started - self.last_finished >= WRITE_QUIET_SECONDS:
                    self.spent = 0.0
                self.owner = identity
            except BaseException:
                self.queue.remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            finished = time.monotonic()
            with self.condition:
                # Count the complete writer step, including acquisition,
                # commit/rollback and Session cleanup. It is conservative.
                self.spent += finished - started
                self.last_finished = finished
                if self.spent >= WRITE_BURST_SECONDS:
                    self.resume_at = finished + WRITE_QUIET_SECONDS
                    self.spent = 0.0
                self.owner = None
                self.queue.popleft()
                self.condition.notify_all()


_budgets: WeakKeyDictionary[Engine, _WriteBudget] = WeakKeyDictionary()
_registry_lock = threading.Lock()


@contextmanager
def writer_turn(bind: Engine | Connection) -> Iterator[None]:
    engine = bind.engine if isinstance(bind, Connection) else bind
    # PostgreSQL 放行：见模块文档「PostgreSQL：必须跳过本预算」。
    # 判据用方言名而不是 `IS_SQLITE`，因为本模块也用于测试注入的临时引擎。
    if engine.dialect.name == "postgresql":
        yield
        return
    with _registry_lock:
        budget = _budgets.get(engine)
        if budget is None:
            budget = _WriteBudget()
            _budgets[engine] = budget
    with budget.turn():
        yield
