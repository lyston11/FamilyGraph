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
