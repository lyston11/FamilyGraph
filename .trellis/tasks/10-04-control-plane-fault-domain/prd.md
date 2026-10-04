# Control plane 与 Agent 故障域隔离

## Goal

让 heartbeat、lease、context、token renewal、cancel、settle、health、recovery 在模型流、工具突发、RAG 维护或 provider retry 过载时仍有独立且有界的执行容量；同时隔离 Assistant 与 Steward 的 sidecar/backend 故障域。

## Requirements

- control-plane 使用独立 AnyIO/worker limiter、独立或保留 DB pool 名额；不得等待 model/tool/background admission。
- backend browser、internal agent、admin、maintenance 明确执行池和优先级；health/metrics 不能被业务执行占满。
- sidecar Assistant/Steward 至少独立槽位、HTTP/SDK 资源和优雅停机边界；评估分进程部署，不能让一个 kind 的 event loop/内存/重试风暴拖垮另一 kind。
- 多实例部署时不依赖进程内 limiter 作为全局事实；lease、capacity、worker identity 和 recovery 由 PostgreSQL 真源裁决。
- 取消、撤权、租约续期、settle 和 shutdown 必须幂等且可恢复；sidecar 重启不得产生双 settle 或孤儿 active slot。

## Dependencies

依赖父任务资源 key、PostgreSQL 真源和现有 internal token/fence/settle 合同；可先在 SQLite 做单实例实验，但最终验收必须在 PostgreSQL 连接池和多实例模型上复验。不依赖 Redis。

## Acceptance Criteria

- execution pool 被完全占满时，control-plane heartbeat/lease/cancel/settle/health 仍在明确预算内完成。
- Assistant 突发不能消耗 Steward/control 保留容量，反之亦然；同一 sidecar 中的 kind 取消信号不互相覆盖。
- sidecar/backend 重启、网络断开、租约过期、重复 settle/recovery 后状态唯一、无泄漏。
- 两实例同时租赁不会重复领取；单实例 limiter 被移除的 mutation 必须由跨实例测试捕获。
- 分进程或分池方案有资源预算、发布顺序、回滚和不改变 token/fence/settle 的证据。
