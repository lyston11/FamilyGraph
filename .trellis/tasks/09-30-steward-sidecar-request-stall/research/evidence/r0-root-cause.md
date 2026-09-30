# 根因确证：事件循环上的 per-chunk 同步 DB 检查 × 连接池耗尽

## 完整机制（全部实测）

```
1. provider 流式转发期间，请求级 Session 持续持有 1 个池连接
   （实测：整个 2.5s 流期间 engine.pool.checkedout() == 1）

2. passthrough_with_audit 是 async generator，**每个 chunk** 在事件循环上
   调用同步的 _refresh_run_gate(db, run_id)（db.rollback() + db.get()）

3. 连接池上限 15（POOL_SIZE 5 + POOL_MAX_OVERFLOW 10），pool_timeout=30s

4. 池一旦被占满（工具执行各占 1 条连接 + 流持有 1 条）：
   → 事件循环上的 db.get() 在 QueuePool 里等待，阻塞**整个事件循环**
   → 每个 chunk 阻塞 30.0s
   → 所有端点（含 /api/health、心跳、lease）一起停
```

## 决定性测量

### 单次阻塞 = pool_timeout

```
PROBE pool exhausted: checkedout=15/15
PROBE _refresh_run_gate on loop: 30.0s -> TimeoutError
PROBE heartbeat coroutine worst lag: 30055ms (samples=4, expected ~50ms)
```

事件循环上的一个心跳协程延迟 **30,055ms**——与 `pool_timeout` 精确一致。

### 累积 = 观测到的停滞时长

```
PROBE pool exhausted 15/15
  chunk 1: 30.0s
  chunk 2: 30.0s
  chunk 3: 30.0s
PROBE 3 chunks -> 90.0s total, wall 90.0s
```

**3 个 chunk = 90 秒**；观测到的停滞为 **96 / 101 / 102 / 111 秒**（3–4 个 chunk）。
量级与机制完全吻合。

### 流期间持续持有连接

```
PROBE baseline=0
PROBE during stream: [('0.0', 0), ('0.6', 1), ('1.2', 1), ('1.8', 1), ('2.4', 1)]
PROBE final=0
```

`checkedout` 在整个流期间为 **1**——请求级 Session 不跨 chunk 归还连接。

## 为什么先前三次测量都没抓到

| 先前测量 | 结论 | 为何不适用 |
|---|---|---|
| 隔离空库测 `_refresh_run_gate` | 3µs，判为「不可能是主因」 | **无竞争**：池未耗尽时确实只要 3µs |
| 持 SQLite 写锁测同一调用 | 0.06s，判为「锁不是原因」 | WAL 下读不阻塞于写锁；瓶颈是**池**不是锁 |
| 池耗尽测 40 个请求 | 30.0s，但**在工作线程内** | 那次测的是 `execute_tool` 路径；本次测的是**事件循环**上的调用 |

**关键区别**：同一个 `db.get()`，
- 在工作线程里阻塞 30s → 只占 1 个线程（严重但不致命）；
- 在**事件循环**上阻塞 30s → **整个进程静默**（致命）。

`execute_tool` 已在上一任务改为「先等名额再进线程」，因此该路径不再在循环上阻塞；
但 `_refresh_run_gate` 仍在事件循环上执行。

## 触发条件（解释 run 232 vs run 238 的差异）

- 停滞需要池**先被占满**。工具并发峰值 8–23，加上流自身持有 1 条，
  达到 15 即可触发。
- run 238 未停滞（心跳最长间隔 26s）说明当时池未被占满。
- 这解释了为何停滞是**间歇性**的：取决于工具执行与流的时序重叠。

## 修复方向

`_refresh_run_gate` 不得在事件循环上做同步 DB I/O。两个必须同时处理的点：

1. **不阻塞事件循环**：per-chunk 的 gate 检查移出事件循环（线程池），
   或改为不依赖连接池等待的路径。
2. **不跨 chunk 持有连接**：检查用的 Session 应在每次检查后立即归还连接，
   否则 100s 的流会一直占 1 条连接（加剧池耗尽）。

保留的合同（R3）：
- per-chunk 取消复核语义不变（取消/失租仍是服务端权威裁决）；
- egress 恰好一次审计；上游 4xx 状态码与不可重试语义；
- fence 检查集、准入 CAS、幂等、审计不变。

**不得**放宽 `pool_timeout` / `AGENT_REQUEST_TIMEOUT_MS` / `defaultLeaseMs` /
`STEWARD_ASSIST_CALL_LEASE_SECONDS`——那只是把失租推迟。

## 复现方法

```python
# 池耗尽 + 事件循环上调用
for _ in range(POOL_MAX_CONNECTIONS):
    engine.connect()          # 占满池
# 在事件循环（非线程）上调用：
provider_proxy._refresh_run_gate(db, run_id)   # -> 30.0s
```
