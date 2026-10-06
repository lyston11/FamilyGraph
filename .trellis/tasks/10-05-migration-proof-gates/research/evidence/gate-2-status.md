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

## 缺口（因此 Gate 2 仍 BLOCKED）

1. **无静态调用图**：当前只确认了 `_settle → fence_execution → acquire_run_writer` 与
   租约 `counter → attempt` 这一条最明显的反向路径。其余 65 个入口中哪些会**同时**
   持有两类锁尚未逐条判定。
2. **三把以上锁未实测**：只验证了两把交叉（counter 与 run 行）。
   `global + kind + tenant + run` 的顺序未做探针。
3. **mutation 用例未补**：设计要求「删 CAS / 删锁 / 删 counter release 必须让测试失败」，
   目前只有入口计数 mutation，没有针对真实业务入口的锁/CAS mutation。
4. **真实入口未在 PostgreSQL 上运行**：三个 B 类入口（`lease_next`、
   `lease_next_steward_job`、`lease_attempt`）的证据是**原型形态**，不是真实入口行为
   （`tx-contracts.json` 已按此标注为 L2）。
5. **`deadlock_timeout` 延迟未测量**：默认 1s 对 lease/settle 延迟预算的影响未知。

## 结论

Gate 2 的**分析与关键机制**已建立且有可复跑证据，但退出门要求（静态调用图、三把以上锁、
真实入口 mutation）未满足。**不得**据此放行 `postgres-migration` 业务实现。
