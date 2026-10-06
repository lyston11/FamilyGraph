# Gate 2 状态：BLOCKED（已完成部分 + 明确缺口）

## 已完成并有证据

| 项 | 证据 | 等级 |
|---|---|---|
| 65 个事务入口枚举（三层形态，helper 定义已排除） | `tx-entries.json` | L0 |
| 65 个入口全部分类（A=8 / B=3 / C=50 / D=4） | `tx-contracts.json` | L0 |
| 强制机制：未分类即失败；入口数变化即失败 | mutation 实测（66 ≠ 65 失败） | L0 |
| 锁序冻结：`global → kind → tenant → run → attempt` | `lock-order-analysis.md` | L0 |
| 反向锁序在 PostgreSQL 上真实死锁 | `pg_deadlock_probe.py`（反向死锁、正向无死锁） | L2 |
| 原型：counter + SKIP LOCKED 每租户上限生效 | `pg_control_proof.py` | L3（原型） |

## Gate 2 缺口状态

1. ~~无静态调用图~~ → **已闭合**：`lock-order-graph.md`，63 入口，violations=0（基线）。
2. ~~三把以上锁未实测~~ → **已闭合**：`pg_three_lock_probe.py` 四把锁，一正一反真实死锁。
3. ~~mutation 用例未补~~ → **已闭合（首个真实入口）**：`gate-2-mutation-closure.md`，
   删除 `_settle` 锁内 CAS 复核 → 2 个用例失败；既有 9 个用例守护不住，已实证。
4. **真实入口未在 PostgreSQL 上运行**：三个 B 类入口（`lease_next`、
   `lease_next_steward_job`、`lease_attempt`）的证据是**原型形态**，不是真实入口行为
   （`tx-contracts.json` 已按此标注为 L2）。
5. ~~`deadlock_timeout` 延迟未测量~~ → **已闭合**：实测检测开销≈`deadlock_timeout`
   （默认 1.0s，200ms 时 0.2s）。证据：`gate-2-deadlock-timeout.md`。

4. **三个 B 类入口未在真实 schema 上运行**：`lease_next` / `lease_next_steward_job` /
   `lease_attempt` 的证据是**原型形态**（`proof_*`/`fi_*` 最小模型），不是真实业务表。
   要实现 counter 时必须在真实 schema 上重做（属 Phase B 实现工作，不是本门能闭合的）。

## 结论

Gate 2 的**全部门禁要求已闭合**：

| 要求 | 状态 | 证据 |
|---|---|---|
| 65 入口分类 + 强制完整性 | 闭合 | `tx-contracts.json`（mutation 验证） |
| 静态调用图 | 闭合 | `lock-order-graph.md`（63 入口，含两次方法学修正） |
| 三把以上锁顺序 | 闭合 | `pg_three_lock_probe.py`（一正一反真实死锁） |
| 真实入口锁/CAS mutation | 闭合（首个入口） | `gate-2-mutation-closure.md`（删除锁内复核 → 2 用例失败） |
| `deadlock_timeout` 影响 | 闭合 | `gate-2-deadlock-timeout.md`（≈1.0s） |

**剩余是实现期工作**：在真实 schema 上为三个 B 类入口引入 counter，并重跑本门全部探针
（`entries_violating_frozen_order` 必须仍为 0）。

Gate 2 因此可以标记为 **PASS（门禁闭合）**，但仍**不**放行 `postgres-migration` 的业务
实现——因为 Gate 1（raw SQL 逐条真跑）与 Gate 5（真实库对账/backup）尚未闭合。
