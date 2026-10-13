# Provider 长流可靠性与上游故障隔离

## Goal

解决 run-level retry 之外的 provider 可靠性问题：长流并发、连接生命周期、stream-level tenant quota、按 upstream/kind 隔离的 circuit breaker、取消/deadline 和重试资源预算。

## Requirements

- 区分 connect/header/stream/response/consumer backpressure 阶段；每阶段有独立 timeout 和安全错误分类。
- provider 建连 admission 不得与长流占用同一个短期名额；长流必须有明确的 account/space/kind/global 并发上限和最大 wall-clock deadline。
- 同一 upstream/DERP 故障只熔断对应 provider profile/kind/租户范围，不影响其他 upstream 或 control-plane。
- HTTP client/连接池复用要经过连接持有、DNS/TLS、取消、流中 gate check 和 egress audit 验证；不得因复用重新跨 chunk 持有 DB 连接。
- `sent`、真实 egress 一次一审计、unknown/计费、cancel/lease lost 语义不变。

## Dependencies

依赖 `RunRetryBudget`、PostgreSQL capacity/resource key 和 control-plane reserve；不把 Redis 作为 circuit 或 lease 真源。必须和 provider proxy 既有错误合同一起验收。

## Acceptance Criteria

- 长流并发被有界控制；一个 account/space/provider 的长流不能占满其他租户或 control capacity。
- DERP/provider 持续失败在单一总预算内结束，circuit 开启后不产生无意义 socket/egress storm。
- stream 中取消、撤权、租约过期均及时停止且 audit/settle 正确。
- client 复用、连接池、DNS/TLS 和 stream backpressure 有真实基准；不以单次成功代替尾延迟验收。
