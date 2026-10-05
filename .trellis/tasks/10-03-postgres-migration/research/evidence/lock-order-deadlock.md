# 锁顺序：迁移到 PostgreSQL 后新增的死锁风险（2026-10-04）

## 结论

`acquire_run_writer` 用 no-op UPDATE 取 **run 行锁**（实测在 PG 上确实串行化，
见下）。而持久化 capacity counter 需要取 **counter 行锁**。两条路径的自然写法
锁序相反，在 PostgreSQL 上会**真的死锁**：

```
A 租约：counter(space) -> run/attempt 行
B 结算：run 行（acquire_run_writer）-> counter(space)
```

实测（交叉顺序，两事务各持一把锁后互等）：

```
死锁被检测到: ['A']
其他结果: [('B', 'ok')]
```

PostgreSQL 检测到并中止了 A（`DeadlockDetected`）。

**SQLite 上不存在这个问题**：`BEGIN IMMEDIATE` 是**全库**写锁，没有「部分顺序」
可言——要么全拿，要么全不拿。迁移把「一把全局锁」换成「多把行锁」后，锁序才成为
必须显式设计的东西。

## 正确的锁顺序

```text
global counter → kind counter → account/space counter → run 行 → attempt 行
```

即：**counter 永远先于 run 行**。由此推出两条实现要求：

1. **租约路径**：先取 counter，再进 fence（fence 内部会取 run 行锁）。
2. **结算路径**：必须在调用 `fence_*_execution`（或任何会取 run 行锁的函数）
   **之前**归还 counter。否则结算路径就是 `run → counter`，与租约相反。

第 2 条是最容易漏的：现有 `record_attempt_outcome` 在调用方事务内运行，而调用方
（`settle_run` 的 `on_settled` 钩子）之前已经过了 `fence_execution`。因此 counter
的归还不能简单地放在 `record_attempt_outcome` 里，必须放在 fence 之前。

## 已验证：no-op UPDATE 确实提供行级串行化

`acquire_run_writer` 的实现是：

```sql
UPDATE agent_runs SET updated_at = updated_at WHERE id = :run_id
```

实测 8 个并发事务执行「取写锁 → 读 max(seq) → 插入」：

```
成功 8，唯一冲突 0
seq 序列: [0,1,2,3,4,5,6,7]
表中事件 8 条，唯一 seq 8 个
```

**反证**（刻意不取写锁，同一并发形状）：

```
无写锁：表中 1 条（期望 8），唯一冲突 7 次
```

因此该机制在 PostgreSQL 上有效，且上面的用例确实守护了它——不是「碰巧没撞」。

## 对迁移的影响

- **不能**只把 `BEGIN IMMEDIATE` 换成行锁就结束：必须同时定义并测试**锁顺序**。
- 需要一条**死锁回归**：并发跑「租约」与「结算」路径，断言不出现
  `DeadlockDetected`，且两者都能完成。变异验证：把 counter 归还移到 fence 之后，
  用例必须失败（或至少暴露交叉顺序）。
- 现有 `acquire_run_writer` 的 4 个调用点
  （`agent_events.append_events`、`context_builder`、两条 fence）都在同一进程内，
  且 PG 下都是行锁，因此**在 SQLite 上永远测不出**这个问题。

## 尚未验证

- 三把以上锁（global + kind + tenant counter + run）下的顺序是否仍无死锁；
  本次只验证了两把锁的交叉情形。
- `SERIALIZABLE` 隔离级别下 PostgreSQL 会改为抛序列化失败而非死锁；当前设计
  不用该级别，但若将来启用需重新验证。
- 死锁检测的**延迟**（`deadlock_timeout` 默认 1s）对 lease/settle 延迟预算的影响
  未测量。
