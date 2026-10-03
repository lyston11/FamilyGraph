# Redis 协调与限流加速层

## Goal

在 PostgreSQL 持久真源之上引入 Redis 的短生命周期 admission、tenant token bucket、缓存和 scheduler wakeup，加速多租户调度但不制造第二套业务事实。

## Scope

- Redis key 设计按 account/space/kind/control-plane/upstream 隔离，定义 TTL、容量、幂等、时钟和回收。
- Redis down/restart、重复消息、丢消息、网络分区时 fail-closed 或回退 PostgreSQL 有界 admission。
- Redis 只做可重建 admission/cache/wakeup/circuit hint；run 终态、attempt 结算、lease 真相、授权和审计仍由 PostgreSQL 提供。
- 评估是否需要 pub/sub/stream/queue；不在没有证据时引入持久 Redis queue。

## Dependencies / non-dependencies

- 依赖父任务的 PostgreSQL 真源和资源 key/fairness 合同；推荐在 PostgreSQL control-plane schema/事务实验后接入。
- 不依赖 pgvector；不允许以 Redis lock 替代 PostgreSQL CAS。
- 必须与运行时资源隔离子任务的 admission 接口兼容，但可单独用故障注入验收。

## Acceptance Criteria

- Redis 可用时能提供有界 tenant admission/cache/wakeup，不改变授权和结算语义。
- Redis 不可用或数据丢失时不会放宽配额，不会丢失持久状态；控制面仍可运行或明确 fail closed。
- 一个 account/space 的 Redis key 突发不能占用其他 tenant 或 control-plane 容量。
- 重复/乱序消息、TTL 到期和重启后的系统能从 PostgreSQL 真源恢复。
- 诊断不输出 key 中的个人数据、prompt、凭据或原始业务值。
