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

### 必须等实现（当前阻塞）

- [ ] 跨实例配额：依赖 `10-03-postgres-migration` 的 capacity counter；
- [ ] stream-level 并发上限：依赖 `10-04-provider-reliability-boundaries`；
- [ ] 真实 schema 的 lease/settle/recovery：依赖 `10-03-postgres-migration` Phase B；
- [ ] Redis 降级策略：依赖 `10-03-redis-coordination`；
- [ ] 检索质量与延迟：依赖 `10-04-lexical-search-migration` + `10-03-pgvector-rag`。

### 可立即完成

- [x] 容量模型文档（连接预算 + 锁序 + 故障矩阵）；
- [ ] 压测场景与通过阈值定义（不需实现即可写）；
- [ ] 故障注入矩阵的执行脚本骨架（待实现填充）。
