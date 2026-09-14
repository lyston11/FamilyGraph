"""Give online SQLite writers an opportunity between Steward write bursts.

SQLite's busy handler polls; a stream of short transactions can repeatedly
take the writer ahead of an already waiting login. All Steward coordinators
using an Engine share this FIFO budget, including delivery and collection.
Waiting happens before creating a Session, never while holding a DB lock.
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
    with _registry_lock:
        budget = _budgets.get(engine)
        if budget is None:
            budget = _WriteBudget()
            _budgets[engine] = budget
    with budget.turn():
        yield
