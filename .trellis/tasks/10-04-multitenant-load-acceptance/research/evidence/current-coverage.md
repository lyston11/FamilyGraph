# 多租户容量验收任务：当前覆盖核查（2026-10-06）

## 结论

本任务是**父任务的最终 release gate**，必须基于已实现的 counter/lease/stream-quota 才能
执行。当前这些实现尚未落地，因此本任务**不能**以「跑一次压测」完成——它需要先有可压测的
对象。

本文件记录：哪些可立即执行的基线已具备、哪些必须等实现、以及「不能宣布什么」。

## 已具备的容量证据（可复用）

| 证据 | 来源 | 等级 |
|---|---|---|
| 连接预算：4 实例安全 / 6 实例超限 | `pg_connection_budget_probe.py` | L2 |
| 每租户配额在并发下生效（counter + SKIP LOCKED） | `pg_control_proof.py` | 原型 L3 |
| 四把锁顺序：正确顺序无死锁、反向死锁 | `pg_three_lock_probe.py` | L2 |
| `deadlock_timeout` ≈ 1.0s 计入延迟预算 | `pg_deadlock_timeout_probe.py` | L2 |
| 六类故障注入（断连/重复 settle/cancel 竞争/租约回收/序列化冲突） | `pg_fault_injection.py` | 原型 L3 |
| 控制面在多租户突发下保住预算 | `test_agent_execution_admission.py` | L1（SQLite） |
| run 级重试预算（失败 run 出站 p50=24 → 封顶 8） | `retry-budget.test.ts` | L1 |

## 必须等实现的部分

| 缺口 | 依赖 |
|---|---|
| 跨实例配额（两实例并发不超额） | `10-03-postgres-migration` 的 capacity counter 落地 |
| stream-level 并发上限 | `10-04-provider-reliability-boundaries` |
| 真实 schema 上的 lease/settle/recovery | `10-03-postgres-migration` Phase B |
| Redis 降级策略 | `10-03-redis-coordination` |
| PGroonga/pgvector 检索质量与延迟 | 两个检索子任务 |
| 连接池在多实例下的真实行为 | `10-04-postgres-operations-cutover` |

## 矩阵状态（诚实标注）

| | 并发 lease | 配额上限 | 取消/撤权 | 故障恢复 | RAG 授权 |
|---|---|---|---|---|---|
| Assistant（account） | 原型 L3 | 原型 L3 | **未测** | 原型 L3 | 未测 |
| Steward（space） | 原型 L3 | 原型 L3 | **未测** | 原型 L3 | 未测 |
| 同用户跨空间 | **未测** | **未测** | 未测 | 未测 | 未测 |
| 多空间并发 | 原型 L3 | 原型 L3 | 未测 | 未测 | 未测 |
| control-plane 保留 | L1（SQLite） | n/a | 未测 | 未测 | n/a |
| Provider 长流 | **未测** | **未测** | 未测 | 未测 | n/a |

「原型 L3」= 在隔离 PostgreSQL 上用最小模型（`proof_*`/`fi_*`）验证过语义，
**不是**真实业务入口或真实多租户负载。

## 明确不能宣布的事

1. **不能**宣布多租户并发已达标——矩阵中「同用户跨空间」「control-plane 保留（跨实例）」
   「Provider 长流」全部未测；
2. **不能**宣布 SQLite 可以退出——writer 切换、真实历史库导入、备份恢复演练均未做；
3. **不能**把原型 L3 当作真实入口 L3——三个配额入口尚未在真实 schema 上运行。

## 本任务可立即执行的部分

- 把上述基线整合为**统一容量模型文档**（已完成于 `pg-connection-budget.md` 与
  `lock-order-graph.md`）；
- 定义压测场景与通过阈值（不需要实现即可写）；
- 定义故障注入矩阵的**执行脚本骨架**，待实现落地后填充。

## 不可立即执行的部分

- 真实多租户压测（无 counter/stream-quota 实现）；
- 跨实例并发验证（无持久化协调）；
- 端到端用户可见验收（无 writer 切换）。
