# C8 多租户容量与故障矩阵复跑（2026-10-23）

## 为什么补这一轮

本任务是父任务的最终 release gate。它的可自主执行部分长期未做，原因是
「需要真实多实例」。本次在**开发环境真实 PostgreSQL** 上补完可自主执行的部分，
并把「需要真实部署」与「已可证明」明确分开。

**环境**：隔离 database `fg_probe_c10`（跑完即 DROP），探针自建 `mt_*` 表，
不碰业务 schema。**线上未接触。**

## 1. 容量与故障矩阵（`multitenant_matrix.py`）

| # | 维度 | 结果 |
|---|---|---|
| 1 | 租户配额（3 account × 6 并发，每租户上限 2） | 各授予 2/6 ✓ |
| 1' | **反证**：无行锁形态 | 授予 6/6 **越限** → 证明行锁承重 |
| 2 | 隔离性（account 1 满载，account 2 申请） | 立即可得，**无饥饿** ✓ |
| 3 | 集群上限（global 上限 3，6 个不同租户） | 授予 3/6 ✓ |
| 4 | 归还恰好一次（同一 run 并发归还 3 次） | 成功 1/3，剩余 0 ✓ |
| 5 | 泄漏检测（全部释放后） | 非零计数行 0 ✓ |
| 6 | 故障恢复（提交**前**断连） | active=0，未留占用 ✓ |
| 7 | 故障恢复（提交**后**断连） | active=1，占用保留可回收 ✓ |
| 8 | 取消 vs 结算（同一 run 并发） | 恰好一个赢家（cancel），终态 cancelled ✓ |

```text
PASS: 多租户容量与故障验收矩阵全部通过（含反证）
```

## 2. 三层配额守恒（`multitenant_acceptance.py`）

```text
1) 租户级配额（space）            占用 6 每租户 {1:2, 2:2, 3:2} 越限 []
2) 集群级上限（cluster_provider）  占用 3（期望 ≤ 3）
3) 流级三层（global/kind/tenant）  占用 3 每租户 {1:1, 2:1, 3:1}（tenant 1, global 4, kind 3）
4) 归还守恒（无泄漏）             归还后非零计数行 0
PASS
```

## 3. 跨实例配额（`cross_instance_capacity.py`）

```text
1) 进程内 limiter（旧形态）
   每实例配置=2 实例=2 观测并发=4 期望集群上限=2
   OK  确实越限（4 > 2），证明进程内状态无法约束集群配额
2) 持久化 counter（现形态）
   集群配置=2 实例=2 观测并发=2 lease 行=2
   OK  未越限（2 <= 2）
PASS
```

## 4. 开发库对账（`reconcile_real_schema.py`，只读）

```text
accounts: 51    family_spaces: 20    agent_runs: 1510
steward_model_calls: 2580    memories: 1
rag_documents: 4    rag_chunks: 6    audit_log: 30491
rag_documents revision 镜像: 一致
attempt/run 孤儿: 无
egress 审计: 1441 个 run，尝试数 min=1 p50=4 max=28
egress sent=false 均带 error_class: 一致
对账通过: 无差异
```

## 5. 依赖解除

本任务原先两条「依赖」已解除：

| 原依赖 | 现状 |
|---|---|
| stream-level 并发上限依赖 `10-04-provider-reliability-boundaries` | `capacity.try_acquire_stream` 已接入真实路径（`internal_agent.py:689`），`test_stream_capacity.py` 6 条 |
| Redis 降级策略依赖 `10-03-redis-coordination` | 隔离 Redis 实例上探针 PASS；降级矩阵已交付 |

## 6. 仍未做（全部需要真实多实例部署）

| 项 | 为什么不能用探针替代 |
|---|---|
| **真实 p50/p95/p99 尾延迟** | 探针测的是配额守恒与故障收敛，不是尾延迟。用探针数字冒充尾延迟验收是本任务 PRD 明确禁止的 |
| provider/DERP 故障注入 | 需真实上游 |
| sidecar crash / 多实例 lease | 需真实多实例 |
| Redis 网络分区 | 需真实网络控制 |
| 用户可见终态核对 | 需真实浏览器链路 |
| 测量 SQLite/单实例基线 | 开发环境已切 `pg_all`，该基线只对迁移前有意义 |

## 7. 结论（不得越界宣称）

**可以宣称**：多租户配额守恒、隔离性（无饥饿）、归还不泄漏、故障恢复收敛、
取消与结算终态唯一——这些在真实 PostgreSQL 上已证，且每项都有反证或对照。

**不可宣称**：「多租户并发已达标」。尾延迟、真实上游故障、真实多实例与用户可见
结果均未测，而 PRD 要求它们共同闭合才能切生产。线上发布由用户手动执行。

## 8. 环境清理

```text
fg_probe_c10：已 DROP（pg_database 中无 %probe% 残留）
开发库 familygraph：未受影响（agent_runs 1510 行不变）
线上：未接触
```
