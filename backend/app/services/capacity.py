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
from typing import Any

from sqlalchemy import select
from sqlalchemy import text as sa_text
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
    # 刻意**不**调用 `db.flush()`：调用方事务里可能持有与本次配额无关的脏状态
    # （例如 `_settle` 的陈旧 run 副本）。在这里 flush 会把那份脏状态一并落库，
    # 覆盖真实终态，使调用方的状态复核失效——实测表现为
    # `test_stale_orm_object_cannot_settle_twice` 不再抛 AGENT_RUN_TERMINAL。
    # 计数行的可见性由调用方事务提交保证；同一会话内后续读取走 identity map。
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
    # 同 `acquire`：不 flush，避免把调用方无关的脏状态一起落库。
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


# ---------------------------------------------------------------- 配额主体辅助
#
# 三个真实租约入口各自的配额维度。`global` 与 `agent_kind` 是所有入口共享的上层，
# 因此每次占用都带它们——否则单租户可以把全库打满。
RESOURCE_ASSISTANT_RUN = "assistant_run"
RESOURCE_STEWARD_JOB = "steward_job"
RESOURCE_STEWARD_ASSIST = "steward_assist"

KIND_ASSISTANT = "assistant"
KIND_STEWARD = "steward"


def assistant_run_specs(*, account_id: int, capacity_account: int) -> list[CounterSpec]:
    """Assistant run 的配额维度：account + agent_kind + global。"""
    return [
        CounterSpec("global", 0, RESOURCE_ASSISTANT_RUN),
        CounterSpec("agent_kind", 0, RESOURCE_ASSISTANT_RUN),
        CounterSpec("account", account_id, RESOURCE_ASSISTANT_RUN),
    ]


def steward_job_specs(*, space_id: int) -> list[CounterSpec]:
    """Steward 确定性内核作业：space + agent_kind + global。"""
    return [
        CounterSpec("global", 0, RESOURCE_STEWARD_JOB),
        CounterSpec("agent_kind", 0, RESOURCE_STEWARD_JOB),
        CounterSpec("space", space_id, RESOURCE_STEWARD_JOB),
    ]


def steward_assist_specs(*, space_id: int) -> list[CounterSpec]:
    """Steward 模型辅助 attempt：space + agent_kind + global。

    space 维度是 ``STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`` 的持久化真相；
    ``_in_flight_for_space`` 的计数查询在 PostgreSQL 上会越限（实测 5/2）。
    """
    return [
        CounterSpec("global", 0, RESOURCE_STEWARD_ASSIST),
        CounterSpec("agent_kind", 0, RESOURCE_STEWARD_ASSIST),
        CounterSpec("space", space_id, RESOURCE_STEWARD_ASSIST),
    ]


def registered(db: Session, specs: Sequence[CounterSpec]) -> bool:
    """这些维度里是否**已经登记**过计数行。

    ## 为什么需要这个判据

    counter 是渐进引入的：登记了才由它裁决配额。未登记时沿用原有计数查询路径，
    这样：

    - SQLite 单测（未 bootstrap counter）行为与改动前**逐字一致**，2018 个既有用例不受影响；
    - PostgreSQL 部署 bootstrap counter 后，配额由持久化计数行裁决（并发下才正确）。

    把「没配置」当成「容量 0」会让尚未登记的入口全部停摆；当成「无限」则等于没接。
    因此必须显式区分。
    """
    return any(
        db.get(AgentCapacityCounter, (s.scope_kind, s.scope_id, s.resource_kind)) is not None
        for s in specs
    )


def try_acquire(db: Session, specs: Sequence[CounterSpec]) -> bool | None:
    """配额占用，三态返回。

    - ``None``：这些维度**未登记** counter → 调用方应走原有计数路径；
    - ``True``：已占用（所有登记维度都成功 +1）；
    - ``False``：某个维度已满 → 调用方应跳过该候选，**不得**靠捕获异常控流。

    只对**已登记**的维度占用：未登记的维度不限制（渐进引入），
    已登记的维度仍然受 ``CHECK (active <= capacity)`` 兜底。
    """
    active_specs = [
        s
        for s in specs
        if db.get(AgentCapacityCounter, (s.scope_kind, s.scope_id, s.resource_kind)) is not None
    ]
    if not active_specs:
        return None
    if not acquire(db, active_specs):
        return False
    return True


def try_release(db: Session, specs: Sequence[CounterSpec]) -> int:
    """归还已登记的维度；未登记的直接跳过。"""
    active_specs = [
        s
        for s in specs
        if db.get(AgentCapacityCounter, (s.scope_kind, s.scope_id, s.resource_kind)) is not None
    ]
    if not active_specs:
        return 0
    return release(db, active_specs)


# ---------------------------------------------------- 行级占用/归还门
#
# 门列**不在 ORM 映射里**（0057 只加数据库列）。原因：ORM 列会让 INSERT/SELECT 带上
# 它们，而迁移拒绝用例会在**中间 revision**（如 0048）上用 ORM 写同一张表，那时列
# 还不存在——实测 `table steward_jobs has no column named capacity_acquired_at`。
# 门是基础设施簿记，不是业务字段，因此用 Core SQL 按需读写。

GATE_TABLE_ATTEMPT = "steward_model_calls"
GATE_TABLE_JOB = "steward_jobs"


def _gate_state(db: Session, *, table: str, row_id: int) -> tuple[Any, Any] | None:
    """读取 (acquired_at, released_at)；行不存在时返回 None。"""
    row = db.execute(
        sa_text(f"SELECT capacity_acquired_at, capacity_released_at FROM {table} WHERE id = :id"),
        {"id": row_id},
    ).first()
    return (row[0], row[1]) if row is not None else None


def mark_acquired(db: Session, *, table: str, row_id: int, now: datetime | None = None) -> None:
    """记录该行已占用名额。只有 counter 已登记（确实占用）时才调用。"""
    db.execute(
        sa_text(f"UPDATE {table} SET capacity_acquired_at = :now WHERE id = :id"),
        {"now": now or timeutil.utcnow(), "id": row_id},
    )


def release_gate(
    db: Session,
    *,
    table: str,
    row_id: int,
    specs: Sequence[CounterSpec],
    now: datetime | None = None,
) -> bool:
    """归还该行占用的名额；**恰好一次**，返回本次是否真的归还了。

    门在行上：`capacity_acquired_at` 非空（确实占用过）且 `capacity_released_at`
    为空（尚未归还）。该条件对同一行只能成立一次，且崩溃后重跑仍成立——已写入的
    `released_at` 会阻止第二次递减。
    """
    moment = now or timeutil.utcnow()
    state = _gate_state(db, table=table, row_id=row_id)
    if state is None:
        return False
    acquired, released = state
    if acquired is None:
        return False  # 未登记 counter 的路径：从未占用，无需归还
    if released is not None:
        return False  # 已归还：第二次调用必须 no-op，否则名额凭空增加
    if release(db, specs, now=moment) == 0:
        return False  # 维度未登记（配置被移除）：不写门，留给诊断发现不一致
    db.execute(
        sa_text(f"UPDATE {table} SET capacity_released_at = :now WHERE id = :id"),
        {"now": moment, "id": row_id},
    )
    return True


def release_attempt(
    db: Session, attempt: Any, *, space_id: int, now: datetime | None = None
) -> bool:
    """归还 assist attempt 占用的名额（五个归还点共用，保证恰好一次）。"""
    return release_gate(
        db,
        table=GATE_TABLE_ATTEMPT,
        row_id=attempt.id,
        specs=steward_assist_specs(space_id=space_id),
        now=now,
    )


def release_job(db: Session, job: Any, *, space_id: int, now: datetime | None = None) -> bool:
    """归还 steward job 占用的名额；行级门保证恰好一次。"""
    return release_gate(
        db,
        table=GATE_TABLE_JOB,
        row_id=job.id,
        specs=steward_job_specs(space_id=space_id),
        now=now,
    )


def release_account_run(
    db: Session, *, account_id: int | None, now: datetime | None = None
) -> bool:
    """归还账户级 assistant run 名额。

    `account_id is None` 表示该 run 不消耗账户并发（steward child run 没有
    AgentSession），直接 no-op——不能把「无账户」当成「账户 0」。
    """
    if account_id is None:
        return False
    moment = now or timeutil.utcnow()
    released = release(
        db,
        assistant_run_specs(account_id=account_id, capacity_account=0),
        now=moment,
    )
    return released > 0


# ---------------------------------------------------- 集群级执行名额（C3/AC-4）
#
# `ResourceLimiter` 的 active 是**进程内**状态，两个实例各自 global_capacity=2 时
# 集群实际并发可达 4（实测见 `scripts/migration-proof/cross_instance_capacity.py`：
# 观测 4，配置 2）。因此执行平面还需要一层**集群级**上限，真相在持久化计数行里。
#
# 分工（两层都必须有）：
#
# - 进程内 limiter：负责**排队与公平**。它是唯一能在事件循环上等待、按租户 aging
#   出队、并有界拒绝的地方；持久化计数行做不到这些（它只能在事务里快速尝试）。
# - 集群级 counter：负责**跨实例总量**。它不排队，只在名额满时立刻返回 False，
#   由进程内层决定是继续等还是拒绝。
#
# 顺序固定：先过进程内（本地排队/公平），再过集群级（总量）。这样等待发生在事件
# 循环上，不占工作线程，也不在数据库事务里阻塞。

RESOURCE_CLUSTER_PROVIDER = "cluster_provider"
RESOURCE_CLUSTER_TOOL = "cluster_tool"


def cluster_specs(resource_kind: str) -> list[CounterSpec]:
    """集群级名额只有一个 global 维度：总量是全局的，不分租户。

    租户维度由进程内 limiter 负责（它知道谁是租户），集群层只回答「全集群还剩几个」。
    """
    return [CounterSpec("global", 0, resource_kind)]


def try_acquire_cluster(db: Session, *, resource_kind: str) -> bool | None:
    """尝试占用一个集群级名额。

    `None` = 未登记（部署未 bootstrap）→ 调用方按「无限」处理，保持既有行为；
    这样未启用集群层的部署与改动前**逐字一致**。
    """
    specs = cluster_specs(resource_kind)
    return try_acquire(db, specs)


def release_cluster(db: Session, *, resource_kind: str) -> int:
    """归还集群级名额；未登记时 no-op。"""
    return try_release(db, cluster_specs(resource_kind))


# ---------------------------------------------------- 流级名额（C4）
#
# 与建连名额的关键区别：建连名额在流开始前就归还，所以一个租户可以同时持有多个
# **已建立**的长流。流级名额覆盖**流的整个生命周期**，因此它是唯一能限制
# 「同时有多少个上游流在跑」的层。
#
# 三层维度：global → agent_kind → tenant。kind 维度防止一类 agent 把另一类挤掉
# （Steward 批量计算 vs Assistant 交互请求的时间尺度不同）。
RESOURCE_PROVIDER_STREAM = "provider_stream"


def stream_specs(*, tenant_kind: str, tenant_id: int, capacity_tenant: int) -> list[CounterSpec]:
    """流级三层维度。`tenant_kind` 只接受 account/space（与 `_execution_tenant_key` 一致）。"""
    kind = KIND_ASSISTANT if tenant_kind == "account" else KIND_STEWARD
    scope_kind = "account" if tenant_kind == "account" else "space"
    return [
        CounterSpec("global", 0, RESOURCE_PROVIDER_STREAM),
        CounterSpec("agent_kind", 0 if kind == KIND_ASSISTANT else 1, RESOURCE_PROVIDER_STREAM),
        CounterSpec(scope_kind, tenant_id, RESOURCE_PROVIDER_STREAM),
    ]


def try_acquire_stream(
    db: Session, *, tenant_kind: str, tenant_id: int, capacity_tenant: int
) -> bool | None:
    """尝试占用一个流级名额。`None` = 未登记 → 不限制（渐进引入）。"""
    specs = stream_specs(
        tenant_kind=tenant_kind, tenant_id=tenant_id, capacity_tenant=capacity_tenant
    )
    return try_acquire(db, specs)


def release_stream(db: Session, *, tenant_kind: str, tenant_id: int) -> int:
    """归还流级名额；未登记时 no-op。"""
    return try_release(
        db,
        stream_specs(tenant_kind=tenant_kind, tenant_id=tenant_id, capacity_tenant=0),
    )
