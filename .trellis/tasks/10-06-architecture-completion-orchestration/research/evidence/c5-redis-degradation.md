# C5：Redis 降级策略

## 冻结的策略

```text
REDIS_DEGRADATION_POLICY = "pg_fallback"
```

**Redis 不可用时回退 PostgreSQL 有界准入，而不是 fail-closed。**

理由：Redis 是加速层，它的失效**不应改变可用性语义**；PostgreSQL 已经有**有界**
准入真源（C2 的 capacity counter，带 `CHECK (active <= capacity)` 兜底），因此回退
是安全的。控制面（heartbeat/lease/settle/cancel/context/health）本来就不走 Redis。

被否决的替代方案 `fail_closed`：把加速层故障升级成用户可见的 503，而本层的全部
价值就是「可选加速」。

## 关键设计：三态而不是布尔

`try_set_if_absent` 返回 `True` / `False` / `None`：

| 返回 | 含义 | 调用方行为 |
|---|---|---|
| `True` | 本层裁决「已获得」 | 直接使用 |
| `False` | 本层裁决「已被占用」 | 直接拒绝 |
| `None` | **本层不可用，无结论** | 走 PostgreSQL 有界路径 |

把 `None` 折成 `False` 会让 Redis 故障时 admission **全部拒绝**（可用性故障）；
折成 `True` 会 **fail-open**（配额失效，比不可用更糟）。因此三态是**安全要求**，
不是风格选择。

## 实测（真实 Redis 7 + 真实故障注入）

`scripts/migration-proof/redis_degradation_probe.py`：

```
1) 基线原子语义
   {'set_nx_first': True, 'set_nx_second': False, 'ttl_seconds': 30}
2) Redis 不可用
   {'raised': 'ConnectionError', 'fail_open': False, 'bounded_seconds': 0.002}
3) Redis 超时
   {'raised': 'ConnectionError', 'elapsed_seconds': 0.001, 'bounded': True}
4) Redis 重启丢 key
   {'before_flush': '1', 'after_flush': None, 'pg_active_survived': True}
PASS
```

关键数字：不可用时 **0.002s** 内抛出 `ConnectionError`，`fail_open=False`。
即故障是**显式有界失败**，不是静默放行。

## 三条硬约束与对应断言

| 约束 | 断言 |
|---|---|
| 绝不 fail-open | 不可用返回 `None`（非 `True`），`fail_open=False` |
| 缓存不承载授权 | key 强制带 scope+epoch；未知 scope 抛 `ValueError` |
| wakeup 丢失可补偿 | 缓存/加速失败只产生 cache miss，持久状态不变 |

## 冷却窗口（延迟有界）

不可用后进入冷却（默认 5s），冷却期内调用**立即**返回。没有它，Redis 故障会让
**每个**请求都付一次连接超时——故障变成固定延迟。

实测：冷却期内 50 次调用 < 0.5s（用例断言）。

## 本次踩到的两个真实问题

1. **超时在隧道/跨可用区下过小**：0.5s 让正常的建连超时，把「网络距离」误报成
   「Redis 不可用」。改为可通过 `REDIS_OPERATION_TIMEOUT_SECONDS` 覆盖，并在文档里
   写明生产默认 vs 跨网络场景的差异。
2. **`RedisAccelerator(None)` 会回落到环境变量**（这是设计意图：显式 None = 用默认
   配置）。因此测「未配置」必须同时清掉环境变量——用例最初因此失败。

## 未完成（诚实声明）

1. **未接入真实 admission 路径**：本层已实现并验证降级语义，但尚未把
   `agent_admission` / 执行准入改为「先问 Redis、不可用则走 PostgreSQL」。
   当前所有准入仍直接走 PostgreSQL（正确但无加速）。
2. **wakeup/pub-sub 未实现**：唤醒机制与「丢消息由周期扫描补偿」的断言未做。
3. **tenant token bucket 未实现**：与 PostgreSQL counter 的一致性未验证。
4. **circuit hint 未实现**：不得成为 lease/settle 真源的断言未做。
5. **eviction / 网络分区 / 多实例 Redis 未注入**。

## 证据等级

降级语义：**L2**（真实 Redis + 真实故障注入 + 变异验证）。
未接入业务路径，因此**不是** L3/L4。
