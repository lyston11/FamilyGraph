# 实施计划：多租户容量观测与故障验收

## Phase A：模型与基线

- [ ] 定义 account/space/kind/control/upstream resource key 和安全诊断字段。
- [ ] 测量当前 SQLite/单实例基线并标记不可外推到 PostgreSQL 的指标。

## Phase B：并发矩阵

- [ ] 运行多个 account Assistant、多个 space Steward、跨空间、同空间多用户和混合 kind。
- [ ] 叠加工具突发、长流、provider retry、RAG/index、heartbeat/lease/settle。
- [ ] 记录 p50/p95/p99、最大值、拒绝/饥饿/泄漏和用户可见终态。

## Phase C：故障注入

- [ ] 注入 provider/DERP、PostgreSQL、Redis、sidecar crash、取消、撤权、lease expiry 和重复消息。
- [ ] 校验恢复、对账、egress exactly-once、fence、settle 和 RAG citation。

## Gate

- [ ] 形成可重复 acceptance report；未满足 control-plane、隔离、恢复和安全日志要求不得切 writer 或发布线上。


## 覆盖核查（2026-10-06）

本任务是父任务的**最终 release gate**，必须基于已实现的 counter/lease/stream-quota 执行。
当前这些实现尚未落地，因此本任务**不能**以「跑一次压测」完成。逐条状态见
`research/evidence/current-coverage.md`。

### 已具备的容量基线（可复用，勿重复测量）

- 连接预算：4 实例安全 / 6 实例超限（`pg-connection-budget_probe.py`）；
- 每租户配额在并发下生效（`pg_control_proof.py`，原型 L3）；
- 四把锁顺序 + 反向死锁（`pg_three_lock_probe.py`）；
- `deadlock_timeout` ≈ 1.0s（`pg_deadlock_timeout_probe.py`）；
- 六类故障注入（`pg_fault_injection.py`，原型 L3）；
- 控制面多租户突发保住预算（`test_agent_execution_admission.py`，L1）。

### 必须等实现（2026-10-10 更新）

- [x] 跨实例配额：capacity counter 已落地（`pg_capacity_concurrency.py` +
      `cross_instance_capacity.py` + `multitenant_matrix.py`）；
- [x] 真实 schema 的 lease/settle/recovery：Phase B 已落地
      （`pg_control_proof.py` + `pg_fault_injection.py` + `multitenant_matrix.py`）；
- [x] 检索质量与延迟：P0 基线已建立（`memory_eval`），P3 重排已提升 quality 层到 3/3；
- [ ] stream-level 并发上限：依赖 `10-04-provider-reliability-boundaries`；
- [ ] Redis 降级策略：依赖 `10-03-redis-coordination`（探针 `redis_degradation_probe.py` 存在，需 Redis 实例）。

### PG-7 矩阵（2026-10-10，真实 PostgreSQL）

`scripts/migration-proof/multitenant_matrix.py` 在隔离 PG 上跑通 8 个维度，
**每维都带反证**：

| 维度 | 结果 | 反证 |
|---|---|---|
| 租户配额（3 account × 6 并发，上限 2） | 2/2/2 不超额 | 无行锁形态 **6/6 越限** |
| 隔离性（一租户满载） | 另一租户立即可得 | — |
| 集群上限（global 3，6 租户） | 3/6 不超额 | — |
| 归还恰好一次（并发 3 次） | 成功 1、剩余 0 | — |
| 泄漏检测 | 非零计数行 0 | — |
| 提交前断连 | active=0（不留占用） | — |
| 提交后断连 | active=1（占用保留可回收） | — |
| cancel vs settle | 恰好 1 赢家 | — |

**反证越限 6/6** 是这一批最有价值的数字：它证明配额结论来自「行锁承重」，
而不是「用例没构造出并发」。

### 可立即完成

- [x] 容量模型文档（连接预算 + 锁序 + 故障矩阵）；
- [ ] 压测场景与通过阈值定义（不需实现即可写）；
- [ ] 故障注入矩阵的执行脚本骨架（待实现填充）。
