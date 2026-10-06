# 实施计划：控制面与执行面故障域隔离

## Phase A：基线

- [ ] 测量 backend AnyIO worker、DB checkout、sidecar event loop/heap、kind slots 和 control latency。
- [ ] 注入 tool burst、长 provider stream、RAG maintenance、retry storm，确认当前共享故障域。

## Phase B：分池

- [ ] 建立 control/execution/background limiter、worker budget、DB reserve 和拒绝码。
- [ ] 将 heartbeat/lease/context/settle/cancel/health 接入 control path，做 cancellation/recovery 回归。
- [ ] 维护 Assistant account 与 Steward space 的独立 slots 和容量。

## Phase C：多实例与 sidecar

- [ ] 用 PostgreSQL lease/capacity 在两个实例上做 duplicate lease、crash recovery、重复 settle 测试。
- [ ] 评估并原型化 Assistant/Steward 分进程或独立 worker；定义部署、优雅停机和回滚顺序。

## Gate

- [ ] execution 满载时 control p95/p99 有界；任一 limiter mutation 必须被回归捕获；不得接触线上部署。


## 覆盖核查（2026-10-06）

本任务**不是从零开始**。AC-1/AC-2 已由 `10-03-agent-resource-isolation` 交付并有回归；
AC-3 部分覆盖（原型 L3）；**AC-4/AC-5 未实现**。逐条证据见
`research/evidence/current-coverage.md`。

### 剩余工作（不重复 AC-1/AC-2）

- [ ] **AC-4 跨实例配额**：当前 limiter 是进程内状态，两个实例的
      `AGENT_EXECUTION_GLOBAL_CAPACITY` 会**相加**，实际并发可达 2× 配置值。
      需要持久化协调（依赖 `10-03-postgres-migration` 的 capacity counter 落地），
      并补「两实例同时租赁不重复领取」回归 + 「移除单实例 limiter 后测试必须失败」的 mutation。
- [ ] **AC-3 补齐**：真实业务 schema 上的故障路径（现为原型）；真实进程 kill；
      membership revoke 期间执行。
- [ ] **AC-5 分进程/分池**：当前 `FG_AGENT_ROLE=both` 时 Assistant/Steward 共用进程，
      共享 event loop/HTTP client/heap/重试风暴。需设计与资源预算。
