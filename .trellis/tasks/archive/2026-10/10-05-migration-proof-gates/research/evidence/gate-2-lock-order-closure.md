# Gate 2 缺口闭合：静态调用图 + 四把锁探针

## 缺口 1：静态调用图 —— 已闭合

`scripts/migration-proof/build_lock_order_graph.py` 从每个事务入口做 DFS，
记录可达的锁类型与行锁顺序。

```
事务入口            63
可达两类以上锁类型   44
违反冻结顺序          0（counter 尚未实现）
```

**方法学修正（两次都踩坑，记录以免重犯）**：

1. 第一版按**行号**排序推断顺序，跨文件比较行号无意义 → 误报 32 个「违反」。
2. 第二版改 DFS 序，但仍把 `global_write_lock` 与行锁一起排序。而它是**事务信封**
   （`BEGIN IMMEDIATE` 在函数体之前取得），包裹整个事务，不可与行锁比较
   → 误报 5 个「违反」。
3. 第三版把信封与行锁分离 → violations 归零，且结论可解释。

**`violations = 0` 不是「顺序正确」的证明**：counter 锁尚未实现，冲突的一方不存在。
本图是**回归基线**——实现 counter 后重跑，该值必须仍为 0。

### 关键发现：租约入口已在取 run 行

| 入口 | 当前行锁 |
|---|---|
| `agent_queue.lease_next` | （无） |
| `steward.lease_next_steward_job` | （无） |
| `steward_assist.lease_attempt` | **run_row** |

`lease_attempt` **已经**取 run 行。因此引入 counter 时它必须排在 run 行**之前**，
否则租约路径自身就是 `run_row → counter`。

结算入口（`_settle`、`settle_attempt`、`settle_steward_job`）同样在函数体开头取 run 行。
**这就是 counter 归还必须早于 fence 的静态证据**——不只是「看起来合理」。

## 缺口 2：三把以上锁 —— 已闭合

`scripts/migration-proof/pg_three_lock_probe.py`，隔离 PostgreSQL，四把锁
（global → kind → tenant → run）：

```
正确顺序（global→kind→tenant→run）: deadlocks=[] results=['B','A']   OK
违反顺序（一正一反）:                deadlocks=['B'] results=['A']    OK
两个不同 tenant counter 并行 0.31s（串行约 0.6s）                      OK
PASS
```

**探针自身修了两次错误（都是用例构造缺陷，不是被测行为）**：

1. 第一版让两个 worker 用**不同** tenant counter —— 资源不相交，永不形成环，
   「未死锁」是用例错误。已改为争用同一 counter 与同一 run。
2. 第二版让两个 worker **都反向** —— 同序永不形成环，再次假通过。
   真实交叉是**一个按冻结顺序（租约：counter 先）、另一个反向（结算：run 先）**。

这两次假通过正是 `design.md` §6 要求区分「实现 bug / 测试 oracle 错误」的实例：
探针报 FAIL 时先怀疑 oracle，而不是宣布安全。

## 仍未闭合（Gate 2 剩余）

- **缺口 3**：针对真实业务入口的锁/CAS mutation（删 CAS、删 counter release 必须失败）。
- **缺口 4**：三个 B 类入口在**真实** PostgreSQL schema 上运行（当前是原型形态）。
- **缺口 5**：`deadlock_timeout`（默认 1s）对 lease/settle 延迟预算的影响未测量。
