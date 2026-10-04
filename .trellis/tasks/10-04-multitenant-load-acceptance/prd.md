# 多租户容量观测与故障验收矩阵

## Goal

建立 account/space/kind/control-plane 的容量模型、脱敏诊断、压测、故障注入和发布验收，避免用单 run 成功代替多租户可用性。

## Requirements

- 建立 global/account/space/kind/control/background/upstream 的容量预算、连接预算、队列上限和 RPO/RTO 目标。
- 覆盖多个 account、多个 space、同用户跨空间、同空间多用户、Assistant+Steward、tool burst、长 provider stream、retry storm、RAG/index 和控制请求。
- 故障注入 connect/DNS/TLS/DERP、PostgreSQL 重启/网络分区、Redis 不可用、sidecar crash、worker cancel、lease expiry、撤权、重复消息和半批失败。
- 诊断只记录安全 resource key、阶段、计数、等待、预算版本、错误类别和时间；不得记录 prompt、正文、凭据、SQL 参数或原始个人数据。
- 报告 p50/p95/p99、最大值、拒绝率、队列饥饿、control-plane 成功率、重复 lease/settle、数据对账和用户可见终态。

## Dependencies

依赖 agent-resource-isolation、postgres-migration、control-plane-fault-domain、provider-reliability-boundaries；RAG/Redis 的故障语义必须纳入矩阵。不进行线上压测。

## Acceptance Criteria

- 形成可重复的 account×space×kind 并发矩阵和故障注入脚本/报告。
- execution 满载时 heartbeat/lease/cancel/settle/health 在目标预算内完成；无租户饥饿或资源泄漏。
- 单一 provider/DERP/数据库/Redis/sidecar 故障不会造成全局级错误放大。
- 所有关键资源、迁移、回滚和安全日志 mutation 都有可判别回归。
- 只在矩阵和对账通过后允许开发环境切换 PostgreSQL；线上仍需用户手动发布。
