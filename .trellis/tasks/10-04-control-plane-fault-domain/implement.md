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
