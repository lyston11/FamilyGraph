"""持久化容量计数（C2）。

## 问题

`FOR UPDATE SKIP LOCKED` 只解决「多个 worker 取到同一个候选行」，**不**解决每租户配额。
实测（`10-05` 的 `pg_control_proof.py`）：READ COMMITTED 下计数子查询读不到并发事务
尚未提交的 `in_flight` 行，每租户上限 2 被放成 **5**。

SQLite 之所以没暴露这个问题，是因为 `BEGIN IMMEDIATE` 提供**全库写锁**——所有写事务
天然串行。换成 PostgreSQL 行锁后，配额必须有**持久化计数行**才能成立。

## 锁序（冻结）

```text
global → agent_kind → account/space → candidate
```

counter 永远先于 run/attempt 行锁。**结算路径必须在 `fence_*_execution`
（内部取 run 行锁）之前归还**，否则构成 `run → counter` 的反向锁序，
已实测在 PostgreSQL 上抛 `DeadlockDetected`。

## 方言差异

| | SQLite | PostgreSQL |
|---|---|---|
| 串行化 | `BEGIN IMMEDIATE` 全库写锁 | 逐行 `SELECT ... FOR UPDATE` |
| 计数行 | 普通读写即可 | 必须先锁再读，否则读到陈旧值 |

两条路径都通过本模块，保证语义一致；差异只体现在取锁方式。

## 不变量

- `active` 恰好等于「已占用名额」的数量；
- `0 <= active <= capacity`（数据库 CHECK 兜底）；
- 占用失败（容量满）必须**不留下**任何增量；
- 归还必须**恰好一次**：门在调用方的状态 CAS（settle 的 `status` 条件更新、
  recovery 的 `applied_at IS NULL`），本模块只负责「减 1 且不为负」。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentCapacityCounter
from app.utils import timeutil

# 冻结的锁序位次：数字越小越先取。
SCOPE_ORDER = {
    "global": 0,
    "agent_kind": 1,
    "account": 2,
    "space": 2,  # account 与 space 同层：它们是不同租户维度，不会同时出现在一条路径上
    "provider": 3,
}


@dataclass(frozen=True)
class CounterSpec:
    """一个容量维度的定位（不含 capacity，避免把配额写进锁序键）。"""

    scope_kind: str
    scope_id: int
    resource_kind: str

    @property
    def order(self) -> int:
        return SCOPE_ORDER[self.scope_kind]


def ensure_counter(
    db: Session,
    spec: CounterSpec,
    *,
    capacity: int,
    now: datetime | None = None,
) -> AgentCapacityCounter:
    """幂等建立计数行。

    已存在时**只更新 capacity**（配置可变），不重置 `active`——重置会让在途租约
    丢失名额归属，从而允许超额。若新 capacity 小于当前 active，保留 active 并由
    CHECK 拒绝（fail-loud），而不是静默截断。
    """
    row = db.get(
        AgentCapacityCounter,
        (spec.scope_kind, spec.scope_id, spec.resource_kind),
    )
    moment = now or timeutil.utcnow()
    if row is None:
        row = AgentCapacityCounter(
            scope_kind=spec.scope_kind,
            scope_id=spec.scope_id,
            resource_kind=spec.resource_kind,
            capacity=capacity,
            active=0,
            version=0,
            updated_at=moment,
        )
        db.add(row)
        db.flush()
        return row
    if row.capacity != capacity:
        row.capacity = capacity
        row.version += 1
        row.updated_at = moment
        db.flush()
    return row


def _lock(db: Session, spec: CounterSpec) -> AgentCapacityCounter | None:
    """按方言取得计数行锁并返回当前行。

    SQLite：`BEGIN IMMEDIATE` 已在事务起点取全库写锁，普通读即可（且 SQLite 不支持
    `FOR UPDATE` 语法）。
    PostgreSQL：必须 `FOR UPDATE`，否则并发事务读到陈旧 `active` 并各自 +1。
    """
    key = (spec.scope_kind, spec.scope_id, spec.resource_kind)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return db.scalar(
            select(AgentCapacityCounter)
            .where(
                AgentCapacityCounter.scope_kind == spec.scope_kind,
                AgentCapacityCounter.scope_id == spec.scope_id,
                AgentCapacityCounter.resource_kind == spec.resource_kind,
            )
            .with_for_update()
        )
    return db.get(AgentCapacityCounter, key)


def acquire(
    db: Session,
    specs: Sequence[CounterSpec],
    *,
    now: datetime | None = None,
) -> bool:
    """按冻结锁序占用所有维度；任一维度满则**全部回滚**并返回 False。

    调用方必须已处于写事务中（SQLite 走 `_immediate_tx`，PostgreSQL 走普通事务）。
    返回 False 表示容量不足——调用方应「跳过该候选看下一个」，而不是捕获异常控流
    （异常会中止整个事务，见 spec 的配额场景）。
    """
    ordered = sorted(specs, key=lambda s: s.order)
    rows: list[AgentCapacityCounter] = []
    moment = now or timeutil.utcnow()

    for spec in ordered:
        row = _lock(db, spec)
        if row is None:
            # 未登记容量 = 不限制（例如 tool 维度尚未配置）。不创建隐式行，
            # 避免把「没配置」误当成「容量 0」。
            continue
        if row.active >= row.capacity:
            # 先检查再写：不能在容量满时先递增再回滚，那样会短暂超额并触发 CHECK。
            return False
        rows.append(row)

    for row in rows:
        row.active += 1
        row.version += 1
        row.updated_at = moment
    db.flush()
    return True


def release(
    db: Session,
    specs: Iterable[CounterSpec],
    *,
    now: datetime | None = None,
) -> int:
    """按冻结锁序归还所有维度；返回实际减 1 的行数。

    门（「恰好一次」）在调用方：settle 用 `status` 条件更新，recovery 用
    `applied_at IS NULL`。本函数只保证 `active` 不为负，并在已为 0 时**不递减**——
    那说明调用方重复归还，属于真缺陷，应通过返回 0 暴露而不是静默吸收。
    """
    ordered = sorted(specs, key=lambda s: s.order)
    moment = now or timeutil.utcnow()
    released = 0
    for spec in ordered:
        row = _lock(db, spec)
        if row is None or row.active <= 0:
            continue
        row.active -= 1
        row.version += 1
        row.updated_at = moment
        released += 1
    db.flush()
    return released


def snapshot(
    db: Session, specs: Sequence[CounterSpec]
) -> dict[tuple[str, int, str], tuple[int, int]]:
    """读取 (active, capacity) 快照，供测试与诊断断言使用。"""
    out: dict[tuple[str, int, str], tuple[int, int]] = {}
    for spec in specs:
        row = db.get(
            AgentCapacityCounter,
            (spec.scope_kind, spec.scope_id, spec.resource_kind),
        )
        if row is not None:
            out[(spec.scope_kind, spec.scope_id, spec.resource_kind)] = (row.active, row.capacity)
    return out
