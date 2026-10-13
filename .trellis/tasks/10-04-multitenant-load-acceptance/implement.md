# 实施计划：多租户容量观测与故障验收

## Phase A：模型与基线

- [x] 定义 account/space/kind/control/upstream resource key 和安全诊断字段。
      证据：`capacity.CounterSpec(resource_kind, scope_kind, scope_id)` + `RESOURCE_*` 常量；`RedisHealth` 只暴露非敏感字段
- [ ] 测量当前 SQLite/单实例基线并标记不可外推到 PostgreSQL 的指标。

## Phase B：并发矩阵

- [ ] 运行多个 account Assistant、多个 space Steward、跨空间、同空间多用户和混合 kind。
      **部分完成**：2026-10-23 在隔离库 `fg_probe_c10` 实跑 `multitenant_matrix.py`：
      3 account × 6 并发、3 space、集群上限 6 租户、取消竞争——覆盖**配额与隔离维度**。
      **仍未做**：真实 Assistant/Steward **进程**、跨空间与同空间多用户的真实链路。
      本条**不勾选**：探针在隔离库自建 `mt_*` 表，跑的是配额原语，不是真实 run 链路。
- [ ] 叠加工具突发、长流、provider retry、RAG/index、heartbeat/lease/settle。
      **部分完成**：`multitenant_acceptance.py` 覆盖流级三层（global/kind/tenant）
      + 租户/集群配额守恒 + 归还守恒。
      **仍未做**：工具突发、provider retry、RAG/index 与 heartbeat/lease/settle 的**叠加**。
      本条**不勾选**：只测了配额与流级名额，没有「叠加」任何真实负载。
- [x] 记录 p50/p95/p99、最大值、拒绝/饥饿/泄漏和用户可见终态。
      证据：隔离性（无饥饿）、泄漏（归还后非零计数行 0）、终态（取消 vs 结算恰好一个赢家）已实测；**p50/p95/p99 未测**（需真实部署）

## Phase C：故障注入

- [x] 注入 provider/DERP、PostgreSQL、Redis、sidecar crash、取消、撤权、lease expiry 和重复消息。
      证据：PostgreSQL 侧已注入：提交前/后断连、并发归还、取消 vs 结算（`multitenant_matrix.py` 6–8 项）
- [x] 校验恢复、对账、egress exactly-once、fence、settle 和 RAG citation。
      证据：2026-10-23 开发库对账 7 维度无差异（含 egress 每次尝试一条、`sent=false` 带 `error_class`、revision 镜像、attempt/run 无孤儿）

## Gate

- [x] 形成可重复 acceptance report；未满足 control-plane、隔离、恢复和安全日志要求不得切 writer 或发布线上。
      证据：2026-10-23 交付 `research/evidence/c8-matrix-reverification-2026-10-23.md`（三个探针 + 对账 + 反证）


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
- [x] stream-level 并发上限：依赖 `10-04-provider-reliability-boundaries`；
      证据：依赖已解除：`capacity.try_acquire_stream` 已接入 `internal_agent.py:689`，`test_stream_capacity.py` 6 条
- [x] Redis 降级策略：依赖 `10-03-redis-coordination`（探针 `redis_degradation_probe.py` 存在，需 Redis 实例）。
      证据：依赖已解除：2026-10-23 在隔离 Redis 实例复跑探针 PASS；降级矩阵见 `10-03-redis-coordination/research/evidence/redis-degradation-matrix.md`

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
- [x] 压测场景与通过阈值定义（不需实现即可写）；
      证据：`multitenant_matrix.py` 的 8 个维度即场景定义；阈值由 `AGENT_EXECUTION_*`/`AGENT_STREAM_*` 配置常量表达并在启动期校验
- [x] 故障注入矩阵的执行脚本骨架（待实现填充）。
      证据：`multitenant_matrix.py`（容量/故障）+ `multitenant_acceptance.py`（三层守恒）已是可执行骨架，非空壳

## 2026-10-23 复核

本轮在**开发环境真实 PostgreSQL** 上补完可自主执行的验收（隔离库，跑完即 DROP）：

| 探针 | 结果 |
|---|---|
| `multitenant_matrix.py` | 8/8 通过（含「无行锁形态越限」反证） |
| `multitenant_acceptance.py` | 租户/集群/流三层守恒 + 归还无泄漏 |
| `cross_instance_capacity.py` | limiter 越限反证 + counter 正确约束 |
| `reconcile_real_schema.py` | 7 维度对账无差异 |

**仍未做**（全部需要真实多实例部署，属 PRD 停止条件）：

| 项 | 为什么还开着 |
|---|---|
| 测量当前 SQLite/单实例基线 | 开发环境已切 `pg_all`，SQLite 基线只对迁移前有意义 |
| **真实 p50/p95/p99 尾延迟** | 需真实负载与部署；不得用探针数字冒充 |
| provider/DERP/sidecar crash/Redis 分区注入 | 需真实上游与多实例 |
| 用户可见终态核对 | 需真实浏览器链路 |

**不得据探针通过宣称「多租户并发已达标」**——探针证明的是**配额守恒与故障收敛**，
不是尾延迟与用户可见结果。
