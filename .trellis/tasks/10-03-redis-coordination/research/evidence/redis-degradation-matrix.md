# Redis 降级矩阵（2026-10-23）

## 这份文件回答什么

`10-03-redis-coordination` 的 coverage 核查把「降级矩阵」列为**可立即写、不需实现**的
项——它需要的是把每个 Redis 用途 × 每种故障形态的行为写成**可判定的表**，而不是更多探针。

本文件先给实测基线，再给矩阵，最后给仍未实现的项。

## 1. 冻结的降级策略

```text
REDIS_DEGRADATION_POLICY = "pg_fallback"
```

**理由**（`redis_accel.py` 模块头已记）：Redis 是加速层，它的失效不应改变可用性语义；
PostgreSQL 已有**有界**准入真源（C2 的 capacity counter），因此回退是安全的。
替代方案 `fail_closed`（Redis 抖动即 503）被否决——它把加速层的故障升级成用户可见的
可用性故障，而本层的全部价值就是「可选加速」。

## 2. 实测基线（真实 Redis 7，隔离实例）

`redis_degradation_probe.py`，隔离容器 `fg-probe-redis`（`127.0.0.1:56379`，跑完即删）：

```text
降级策略（产品决策，冻结为）: pg_fallback
1) 基线原子语义
   {set_nx_first: True, set_nx_second: False, ttl_seconds: 30}
2) Redis 不可用
   {raised: 'ConnectionError', fail_open: False, bounded_seconds: 0.001}
3) Redis 超时
   {raised: 'ConnectionError', elapsed_seconds: 0.001, bounded: True}
4) Redis 重启丢 key
   {before_flush: '1', after_flush: None,
    pg_active_survived: True, note: 'Redis key 丢失不影响 PostgreSQL 持久状态'}
PASS: Redis 故障为显式有界失败；持久状态不依赖 Redis
```

四条关键事实：`SET NX EX` 单赢家成立；不可用时**显式抛错**（不静默放行）；
失败**有界**（0.001s，不是挂死）；Redis 数据丢失**不影响** PostgreSQL 持久状态。

## 3. 降级矩阵

用途取自 `redis_accel.py` 的实际实现（负缓存 + 通用 KV + key 规范），
不写「将来可能会有」的用途。

| Redis 用途 | 不可用 | 超时 | 数据丢失（flush/eviction） | 恢复后 |
|---|---|---|---|---|
| **负缓存命中判定**（`is_marked_over_quota`） | 返回 `False` → 走 PostgreSQL 权威路径 | 同左（冷却窗口内不再尝试） | 标记消失 → 走权威路径，**只是少了快速拒绝** | 自动重新填充，无需补偿 |
| **负缓存写入**（`mark_over_quota`） | 返回 `False`，调用方忽略 | 同左 | 不适用 | 下次权威判定时重写 |
| **负缓存清除**（`clear_over_quota`） | 返回 `False` → 假拒绝窗口延长至 TTL（默认 1s） | 同左 | 标记本就消失 | — |
| **admission 单赢家**（`try_set_if_absent`） | 返回 `None`（**无结论**）→ 调用方走 PG 有界路径 | 同左 | key 消失 → 可能重复取得，由 PG counter 兜底 | 无需补偿 |
| **通用缓存读写**（`get`/`set`/`delete`） | `get`→`None`（cache miss）、`set`/`delete`→`False` | 同左 | cache miss，重新计算 | — |
| **客户端依赖缺失**（`import redis` 失败） | 降级为不可用（`available()=False`），**不崩溃** | 不适用 | 不适用 | 装上依赖后下次进程启动生效 |

### 三条硬约束在矩阵中的落点

1. **绝不 fail-open**：每一行的「不可用」列都不出现「放行」。负缓存不可用 = 没有标记
   = 照常走权威路径（可能**拒绝**，绝不**放行**）。
2. **缓存不承载授权**：缓存只存「已由 PostgreSQL 授权过的结论」的短期投影，key 必须带
   `scope` + `epoch`（`scoped_key` 强制 `scope_kind ∈ {global, account, space, agent_kind}`，
   未知即 `ValueError`）。缓存失效只会导致 cache miss。
3. **wakeup 丢失可补偿**：所有 wakeup 都是**提示**，持久扫描是真相；丢一条只意味着延迟。

### 有界性

失败必须有界。`_UNAVAILABLE_COOLDOWN_SECONDS`（默认 5s）保证 Redis 挂掉时**不是每个请求**
都去等一次超时；`_operation_timeout()`（默认 0.5s，隧道/跨可用区须调大）限制单次操作。

实测 0.001s（同机，连接被拒立即返回）远小于 0.5s 上限。

## 4. 本轮修掉的真实缺陷：`redis` 未被声明为运行时依赖

### 症状

`familygraph-prod-api-1` 内 `import redis` 直接 `ModuleNotFoundError`，
而本机 venv 正常（历史上手工装过）。`backend/pyproject.toml` 的依赖列表**没有** `redis`，
任何 requirements 文件也没有。

### 危害不是「功能缺失」而是「崩溃」

`redis_accel._client_or_none()` 里的 `import redis` 原本在 try 块**之外**：

```python
def _client_or_none(self):
    ...
    import redis          # ← 在 try 之外
    ...
    try:
        return bool(client.set(...))
    except Exception as exc:   # 「任何后端错误都按不可用降级」
        self._mark_unavailable(exc)
```

因此 `ModuleNotFoundError` **不会**被降级的 except 捕获；而 `available()` 此时仍返回
`True`（它只看 url 非空）。调用方于是以为加速层健康，随后在读写路径上抛异常。

实测（屏蔽 `import redis` 后走真实代码路径）：

```text
configured: True
available(): True
RAISED: ImportError No module named redis     ← 修复前
```

### 修法（两处，缺一不可）

1. `pyproject.toml` 声明 `redis==8.1.0` 为**运行时**依赖——与紧邻的 `psycopg` 同类理由：
   API 容器以 `pip install .` 构建，未声明的依赖不会进镜像，而错误在发布脚本里表现为
   「容器不 healthy」，很难从症状反推到依赖。
2. `import redis` 移入 try 并降级。依赖缺失是**部署错误**，但仍不得让调用方崩溃。

修复后：

```text
redis accelerator degraded policy=pg_fallback error_class=ImportError cooldown_s=5.0
redis accelerator unavailable: client library missing; policy=pg_fallback (install the `redis` runtime dependency)
configured: True
get -> None        set -> False        try_set_if_absent -> None
health: RedisHealth(available=False, policy='pg_fallback', last_error='ImportError', ...)
OK: 降级而非崩溃
```

### 回归与反证

新增 `backend/tests/test_redis_missing_client.py`——**单独一个文件**，因为
`test_redis_degradation.py` 有模块级 `pytestmark` 要求 `REDIS_URL`，会把这条用例一并 skip，
而它要防的恰恰是「本地 venv 恰好装过、镜像里没装」的形态。

**反证**：把 `import redis` 还原到 try 之外后，该测试**失败**（已实测）。

## 5. 仍然未实现的（诚实声明）

矩阵是**行为合同**，不是实现清单。以下仍开着：

| 项 | 为什么还开着 |
|---|---|
| wakeup / pub-sub | 未实现。设计已定（wakeup 只是提示、持久扫描是真相），但代码里没有 pub/sub 路径 |
| token bucket | **刻意不做**：令牌由 Redis 持有并授权就等于把 Redis 变成配额真源，与 `pg_fallback` 策略矛盾。现形态是**负缓存**（只能拒绝，永不授权） |
| circuit hint | 未实现。约束已定：circuit 状态可缓存，但不得由 Redis 单独裁决上游可用性 |
| 租户级 queue/saturation 诊断 | 未实现 |
| RAG/Provider cache 的 revision/scope/visibility key 失效测试 | `scoped_key` 已强制 scope+epoch，但没有 RAG/Provider 级缓存的消费者 |
| 多 account/space 并发 + Redis down/restart/分区矩阵 | 需要真实多实例部署 |
| 降级策略的**实现**接入 admission | 负缓存形态**已接入**（`is_marked_over_quota` 等）；wakeup/token bucket 未接 |

## 6. 环境清理

```text
隔离 Redis fg-probe-redis（56379）：已 stop + rm
临时依赖目录 /tmp/fg-redis-lib：已删除
novelagent-redis（另一项目的容器）：未触碰
开发库 familygraph：未受影响（agent_runs 1510 行不变）
线上：未接触
```
