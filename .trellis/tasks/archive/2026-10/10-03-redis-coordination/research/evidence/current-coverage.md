# Redis 协调任务：当前覆盖核查（2026-10-06）

## 结论

**Redis 自身的语义已验证可用**（见 `redis-degradation-probe.md`），但本任务的核心——
**降级策略**——尚未实现。因此本任务的主要工作是代码，而不是更多 Redis 探针。

## 已覆盖（勿重复）

`scripts/migration-proof/redis_degradation_probe.py`（真实 Redis 7）：

| 检验 | 结果 |
|---|---|
| `SET NX EX` 单赢家（20 次并发） | 成功 **1** 次 |
| TTL 生效 | 25s（>0 且 ≤30） |
| `INCR` 原子单调 | 1..5 无跳号 |
| 连接失败 | 抛 `ConnectionError`（**fail-loud**，不静默放行） |

结论：Redis 的 CAS/TTL/原子计数语义成立，且不可用时**显式抛错**，因此可作为加速层。

## 未实现（本任务的实际工作）

| 项 | 为什么重要 |
|---|---|
| **降级策略** | Redis 不可用时 admission 是回退 PostgreSQL 还是有界 fail-closed？**必须有明确答案与回归**，否则「Redis 挂了」会变成「限流失效」 |
| **缓存失效不得放宽授权** | 缓存的是策略/额度；缓存 miss 或过期**不得**让原本被拒的请求通过 |
| **wakeup/pub-sub 丢失** | 丢失只能导致延迟，不能导致漏执行或重复执行 |
| **与 PostgreSQL counter 的一致性** | Redis 是加速层，counter 真源在 PostgreSQL；两者不一致时以 PostgreSQL 为准，且不得出现「Redis 放行但 PG 超额」 |
| **circuit hint 不得成为真源** | circuit 状态可缓存，但不得由 Redis 单独裁决「上游可用/不可用」 |

## 设计约束（来自父任务，不可放宽）

- Redis **只**承担有明确 TTL/失效/不可用语义的协调、限流、缓存与 wakeup；
- 持久业务事实、lease 终态、attempt 结算、审计**仍以 PostgreSQL 为真源**；
- Redis 不得成为迁移期间的**第二事实来源**。

## 本任务可立即完成的

- [x] Redis 语义验证（已完成）；
- [ ] 降级矩阵设计（不需实现即可写）：每个 Redis 用途 × 不可用/超时/数据丢失时的行为；
- [ ] 上述设计的回归骨架。

## 不可立即完成的

- 降级策略的实现与回归（需先有 PostgreSQL counter，依赖 `10-03-postgres-migration`）；
- 与 counter 一致性的验证（同上）；
- 多实例限流验证（需先有跨实例配额）。
