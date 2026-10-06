# Gate 2 缺口 3 闭合：真实入口的锁/CAS mutation

## 发现：既有测试守护不住锁内 CAS 复核

对真实业务入口做变异：删除 `_settle` 里的**锁内终态复核**（`run.status in
RUN_TERMINAL_STATUSES`）。

结果：**既有 9 个 settle/cancel 用例全部仍然通过**。

原因是入口处还有一次廉价检查，单线程用例总在那里就被挡住，锁内复核从未被触发。
这正是 `database-guidelines.md` 记录过的陷阱：

> 仅含条件 UPDATE 的竞态，结果断言在 pysqlite 上测不出锁缺失。

## 两次失败的尝试（记录以免重犯）

| 尝试 | 做法 | 结果 |
|---|---|---|
| 1 | 两个线程 + barrier 并发 `settle_run` | 删掉锁内复核后**仍通过**——SQLite 的 `BEGIN IMMEDIATE` 把两者串行化，后到者往往在入口就看到终态 |
| 2 | 两个线程 cancel vs settle | 同上，测不出 |

结论：**「并发线程 + barrier」在 SQLite 上不足以守护锁内复核**。需要构造
「入口检查通过、进入事务后状态已变」的**陈旧对象**形状。

## 最终用例（`tests/test_agent_queue_settle_race.py`）

1. `test_stale_orm_object_cannot_settle_twice`：持有陈旧 `AgentRun`（内存里
   `status='leased'`，数据库已是 `succeeded`）再结算一次，必须被锁内复核拒绝。
2. `test_the_two_terminal_cas_rechecks_are_both_load_bearing`：AST 断言
   `settle_run` 的入口检查与 `_settle` 的锁内复核**都存在且行号不同**
   （复制粘贴同一处不算两处）。
3. `test_concurrent_settle_has_exactly_one_winner`、`test_terminal_run_cannot_be_resettled_after_concurrent_cancel`：
   保留并发形状作为补充（并诚实注明它们**测不出**锁内复核缺失）。

**变异验证**：删除锁内复核 → 2 个用例失败（含结构性断言）。恢复 → 4 个通过。

## 附带结论：并发用例的价值边界

这两个并发用例**不能**守护锁内复核，但仍有价值：它们守护「终态唯一」这一结果
性质（例如 `run.settled` 事件不重复）。把它们当作锁内复核的守护者是错的，
因此文件里逐条写明了哪条用例守护什么、哪条测不出来。
