# 实施计划：Redis 协调与限流加速层

## Phase A：不接业务的原型

- [ ] 启动隔离 Redis，定义 env/version/kind/account/space key 命名、TTL、最大容量和指标。
- [ ] 实现 token bucket/admission/cache/wakeup 最小接口，所有持久结果仍写 PostgreSQL/测试真源。
- [ ] 建立 Redis unavailable、timeout、restart、eviction 和网络分区注入。

## Phase B：Admission 接入

- [ ] 先接非控制面的限流/快速拒绝；control-plane 保留 PostgreSQL 直通容量。
- [ ] 验证重复 request、sidecar 重试、进程崩溃、TTL 到期不会重复扣额或放宽配额。
- [ ] 接入租户级 queue/saturation 诊断，脱敏 key 和指标维度。

## Phase C：Wakeup/cache/circuit hint

- [ ] 使用 pub/sub 或短消息做唤醒，但保留周期扫描补偿丢消息。
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
