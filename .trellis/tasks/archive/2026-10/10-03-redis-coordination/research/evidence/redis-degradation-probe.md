# Redis 协调层语义探针

由 `scripts/migration-proof/redis_degradation_probe.py` 生成（真实 Redis 7）。

| 检验 | 结果 |
|---|---|
| `SET NX EX` 单赢家（20 次并发） | 成功 1 次 ✅ |
| TTL 生效 | >0 且 ≤30s ✅ |
| `INCR` 原子单调 | 1..5 无跳号 ✅ |
| 连接失败 | 抛异常（fail-loud）✅ |

## 结论

Redis 的 CAS/TTL/原子计数语义成立，可作**加速层**。

## 未覆盖（关键）

本探针**只验证 Redis 自身语义**，未验证 FamilyGraph 的降级策略：

- Redis 不可用时 admission 是回退 PostgreSQL 还是有界 fail-closed？
- 缓存失效是否会导致授权放宽？
- wakeup/pub-sub 丢失时的行为？

这些需要代码实现与独立回归，属 `10-03-redis-coordination` 的实施工作。
