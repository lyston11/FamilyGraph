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

## 缺口（Gate 2 剩余）

1. ~~无静态调用图~~ → **已闭合**：`lock-order-graph.md`，63 入口，violations=0（基线）。
2. ~~三把以上锁未实测~~ → **已闭合**：`pg_three_lock_probe.py` 四把锁，一正一反真实死锁。
3. ~~mutation 用例未补~~ → **已闭合（首个真实入口）**：`gate-2-mutation-closure.md`，
   删除 `_settle` 锁内 CAS 复核 → 2 个用例失败；既有 9 个用例守护不住，已实证。
4. **真实入口未在 PostgreSQL 上运行**：三个 B 类入口（`lease_next`、
   `lease_next_steward_job`、`lease_attempt`）的证据是**原型形态**，不是真实入口行为
   （`tx-contracts.json` 已按此标注为 L2）。
5. **`deadlock_timeout` 延迟未测量**：默认 1s 对 lease/settle 延迟预算的影响未知。

## 结论

Gate 2 的**分析与关键机制**已建立且有可复跑证据，但退出门要求（静态调用图、三把以上锁、
真实入口 mutation）未满足。**不得**据此放行 `postgres-migration` 业务实现。
