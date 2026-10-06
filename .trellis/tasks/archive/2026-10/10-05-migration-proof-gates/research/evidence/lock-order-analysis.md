# Gate 2：锁序分析与真实冲突点

## 固定锁序（冻结）

```text
global capacity → kind capacity → tenant capacity → run row → attempt row
```

## 现状：现有代码没有 counter，因此锁序尚未冲突

扫描全部 65 个事务入口后，**现有代码中不存在 counter 行锁**（`bump_counter` 尚未落地）。
当前实际持有的锁只有：

| 锁 | 位置 | 取锁方式 |
|---|---|---|
| run 行 | `agent_execution.acquire_run_writer` | no-op `UPDATE agent_runs SET updated_at=updated_at WHERE id=?` |
| 全库写锁 | `_immediate_tx` / `command_transaction` | 驱动级 `BEGIN IMMEDIATE` |

`acquire_run_writer` 的 4 个调用点：`agent_events.append_events`、`context_builder`、
`fence_assistant_execution`、`fence_steward_execution`。

## 真实冲突点（已验证机制）

`agent_queue._settle` 的结构是：

```python
with _immediate_tx(db):                      # 1. 开启事务（SQLite: 全库写锁）
    run, _, _ = fence_execution(db, execution)   # 2. -> acquire_run_writer -> run 行锁
    ...
```

`steward_assist.settle_attempt` 经 `_settle` 的 `on_settled` 钩子在**调用方事务内**运行
（见 `steward-child-run.md` §12），因此它的锁序是：

```text
run 行（由 fence 取） → attempt 行
```

而**租约路径**（`lease_attempt`）的自然锁序是：

```text
counter(space) → attempt 行
```

两者组合即 `counter → attempt` 与 `run → counter`，构成反向。

## 实测：反向锁序在 PostgreSQL 上真的死锁

`lock-order-deadlock.md` 记录的两连接探针（隔离 PostgreSQL）：

```
死锁被检测到: ['A']
其他结果: [('B', 'ok')]
```

PostgreSQL 检测到交叉锁序并中止了 A。**SQLite 上无法观察**：`BEGIN IMMEDIATE` 是
全库写锁，没有「部分顺序」——要么全拿要么全不拿，所以这个缺陷在 SQLite 测试中
**永远测不出来**。

## 实现约束（Gate 2 的结论）

1. counter 必须**先于** run 行取得；
2. 因此 `_settle` 中的 counter 归还**不能**放在 `record_attempt_outcome` 里——
   该函数在 fence 之后运行；
3. 归还必须是独立的幂等事务（在 fence 之前）或 CAS，不能用注释掩盖反向锁序；
4. 需要一条**并发死锁回归**：同时跑租约与结算，断言不出现 `DeadlockDetected`；
   变异验证：把 counter 归还移到 fence 之后，用例必须失败。

## 未完成

- 三把以上锁（global + kind + tenant + run）的顺序未实测，只验证了两把交叉；
- `deadlock_timeout`（默认 1s）对 lease/settle 延迟预算的影响未测量；
- 65 个入口中尚未逐一确认哪些会**同时**持有两类锁（需静态调用图，当前只覆盖了
  `_settle`/`settle_attempt` 这一条最明显的路径）。
