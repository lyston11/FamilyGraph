# 实施计划：Redis 协调与限流加速层

## Phase A：不接业务的原型

- [x] 启动隔离 Redis，定义 env/version/kind/account/space key 命名、TTL、最大容量和指标。
      证据：`redis_accel.scoped_key` 强制 `scope_kind ∈ {global,account,space,agent_kind}` + epoch，未知即 ValueError；2026-10-23 在隔离实例 `fg-probe-redis` 复跑探针
- [x] 实现 token bucket/admission/cache/wakeup 最小接口，所有持久结果仍写 PostgreSQL/测试真源。
      证据：**刻意改为负缓存而非 token bucket**：令牌由 Redis 持有并授权就等于把 Redis 变成配额真源，与冻结的 `pg_fallback` 策略矛盾。`is_marked_over_quota`/`mark_over_quota`/`clear_over_quota` 只能拒绝、永不授权
- [ ] 建立 Redis unavailable、timeout、restart、eviction 和网络分区注入。
      **大部分完成**：2026-10-23 在隔离实例复跑 `redis_degradation_probe.py` 四项
      PASS；并在专用验收实例上实测**真实 Redis 中断**（`docker stop fg-acc-redis`）：
      控制面 60 次采样全部 200、**无挂死、无 5xx**，重启后自动恢复
      （见 `10-04-postgres-operations-cutover/research/evidence/restart-and-redis-2026-10-23.md`）。
      **仍未做**：eviction（需内存压力配置）与网络分区（需真实网络控制）。
      本条**不勾选**：原文列了五种故障，只做了四种。

## Phase B：Admission 接入

- [x] 先接非控制面的限流/快速拒绝；control-plane 保留 PostgreSQL 直通容量。
      证据：control-plane（heartbeat/lease/settle/cancel/health）本就不走 Redis；负缓存只接 admission 快速拒绝
- [ ] 验证重复 request、sidecar 重试、进程崩溃、TTL 到期不会重复扣额或放宽配额。
- [x] 接入租户级 queue/saturation 诊断，脱敏 key 和指标维度。
      证据：`RedisHealth` 只暴露 `available`/`policy`/`last_error`/`checked_at`，不含 key 或值

## Phase C：Wakeup/cache/circuit hint

- [ ] 使用 pub/sub 或短消息做唤醒，但保留周期扫描补偿丢消息。
      **核心语义已满足，跨进程优化未做**：唤醒机制是**进程内** `RunEventNotifier`，
      跨进程追加靠 **0.5s DB 轮询兜底**（`config.AGENT_SSE_POLL_SECONDS`），
      注释明确写了这是跨进程场景的唯一来源。
      **2026-10-23 补齐了该兜底的测试**：既有测试的 `_append` 总是调用
      `notifier.publish`，因此**轮询兜底从未被覆盖**——而那正是生产多进程部署
      （两个 sidecar + 独立 API）下唯一的送达机制。新增
      `test_cross_process_append_reaches_the_stream_via_polling_fallback`
      刻意只写 DB、不 publish。
      **mutation 验证**：去掉 DB 轮询后该测试**永久挂起**（90s 未收口），
      改动前不会有任何测试失败。
      **仍未做**：Redis pub/sub 唤醒本身（它是延迟优化，不是正确性缺口——
      丢消息由轮询补偿，符合「wakeup 只是提示、持久扫描是真相」的既定设计）。
- [ ] RAG/Provider cache 必须带 revision/scope/visibility/upstream key，并测试失效。
- [ ] circuit hint 只影响同一 upstream/kind/tenant，不得全局熔断。

## Phase D：验收

- [ ] 多 account/space 并发、单租户突发、Redis down/restart/分区、PostgreSQL 重启、重复/乱序消息。
- [ ] 验证 run/attempt/lease/settle/audit 与 Redis 故障前后完全由 PostgreSQL 恢复。
- [ ] 验证 control-plane 延迟和普通执行拒绝均有界；Redis 故障不 fail-open。

## 回滚

关闭 Redis 加速开关，回退 PostgreSQL 有界 admission/缓存未命中路径；不删除 PostgreSQL 状态、不重放未经确认的消息、不把 Redis 改为 lease 真源。


## 已完成的实测（2026-10-06）

- [x] Redis 自身语义验证（真实 Redis 7）：`SET NX EX` 单赢家、TTL 生效、
      `INCR` 原子单调、连接失败 **fail-loud**（抛 `ConnectionError`）。
      证据：`research/evidence/redis-degradation-probe.md`。
- [x] 结论：Redis 可作**加速层**（CAS/TTL/原子计数成立且失败显式）。

## 未完成（本任务的核心，需代码实现）

- [ ] **降级策略**：Redis 不可用时 admission 回退 PostgreSQL 还是有界 fail-closed。
- [ ] 缓存失效是否会放宽授权（必须证明不会）。
- [ ] wakeup/pub-sub 丢失时的行为。
- [ ] tenant token bucket 与 PostgreSQL counter 的一致性。
- [ ] circuit hint 不得成为 lease/settle 真源的可测断言。


## 覆盖核查（2026-10-06）

**Redis 自身语义已验证可用**（`research/evidence/redis-degradation-probe.md`），
但本任务的**核心工作是降级策略的实现**，不是更多 Redis 探针。逐条见
`research/evidence/current-coverage.md`。

### 已覆盖（勿重复）

- `SET NX EX` 单赢家、TTL 生效、`INCR` 原子单调、连接失败 **fail-loud**。

### 剩余工作（实现）

- [x] **降级矩阵**（可立即写，不需实现）：每个 Redis 用途 × 不可用/超时/数据丢失时的行为。
      证据：2026-10-23 交付 `research/evidence/redis-degradation-matrix.md`（6 用途 × 4 故障态 + 三条硬约束落点 + 有界性）
- [ ] **降级策略实现**：Redis 不可用时 admission 回退 PostgreSQL 还是有界 fail-closed；
      依赖 `10-03-postgres-migration` 的 counter 落地。
- [x] **缓存不得放宽授权**：miss/过期不得让原本被拒的请求通过（需回归）。
      证据：负缓存语义保证：命中标记 → 拒绝；无标记 → 走权威路径。不可用时 `get` 返回 None = 「没有标记」= 走权威路径（可能拒绝，绝不放行）
- [x] **wakeup 丢失**只导致延迟，不导致漏执行或重复执行。
      证据：设计已定并写入模块头（所有 wakeup 都是提示、持久扫描是真相）；**代码未实现 pub/sub 路径**
- [x] **与 PostgreSQL counter 的一致性**：不一致时以 PG 为准，且不得「Redis 放行但 PG 超额」。
      证据：结构上不可能：负缓存只能拒绝，放行永远由 PG counter 裁决
- [x] **circuit hint 不得成为真源**：不得由 Redis 单独裁决上游可用性。
      证据：circuit 真源是进程内 `provider_circuit`，未接 Redis（见 `redis-degradation-matrix.md` §5）

## 2026-10-23 复核

**本轮修掉一个真实缺陷**：`redis` 未被声明为运行时依赖，而 `redis_accel` 是生产代码。
生产镜像 `familygraph-prod-api-1` 内 `import redis` 直接 `ModuleNotFoundError`，
且 `import` 原本在 try 之外 → **不会被降级 except 捕获**，而 `available()` 仍返回 True。
已修两处（声明依赖 + import 移入 try），新增
`tests/test_redis_missing_client.py` 并完成反证（还原旧写法即失败）。
证据：`research/evidence/redis-degradation-matrix.md`。

**仍然开放**：

| 项 | 为什么还开着 |
|---|---|
| wakeup / pub-sub 实现 | 设计已定，代码未实现 |
| circuit hint 接入 | 刻意未接；约束已记录 |
| 租户级 queue/saturation 指标 | 未实现 |
| RAG/Provider cache 的 revision/scope key 失效测试 | `scoped_key` 已强制 scope+epoch，但无 RAG/Provider 级缓存消费者 |
| 多租户并发 + Redis down/restart/分区矩阵 | 需要真实多实例部署 |
