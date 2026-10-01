# 技术设计：池与事件循环可观测性

## 1. 落点

新增 `backend/app/services/runtime_diagnostics.py`，提供三件事：

### 1.1 连接池等待测量（SQLAlchemy pool 事件）

`QueuePool` 的 `checkout` 事件只在**等待之后**触发，拿不到等待时长。因此：

- 在 `_do_get` 前后打点不可行（不 hook 私有方法）；
- 改用 **pool 事件 `checkout` 记录「本次 checkout 的排队时长」不可得**，
  所以改为在**应用层**测量：`SessionLocal()` 之后第一次 SQL 之前的耗时 ≈
  等待连接的时长（因为 `SessionLocal()` 本身惰性，不取连接；第一次 execute 才取）。

**更可靠的做法**：用 `engine.pool` 的 `checkout`/`checkin` 事件维护
`checkedout` 计数与**最近一次 checkout 的等待时长**——等待时长通过
「发出请求时刻」与「checkout 事件触发时刻」的差得到，需要包装 `engine.connect`。

**选定方案**：包装 `engine.connect()`：

```python
def connect_with_timing(self):
    t0 = perf_counter()
    conn = _orig_connect()
    waited = perf_counter() - t0
    if waited > THRESHOLD:
        log("db_pool_wait", waited_ms, checkedout, size, overflow)
    return conn
```

因为 `engine.connect()` 在池满时**正好**阻塞在 checkout，所以这段差值就是等待时长。
不 hook 私有方法，不改池实现。

### 1.2 事件循环延迟

用 asyncio 任务周期性测量「调度延迟」：

```python
async def _loop_lag_probe(stop):
    while not stop:
        t0 = loop.time()
        await asyncio.sleep(INTERVAL)
        lag = loop.time() - t0 - INTERVAL
        if lag > THRESHOLD:
            log("event_loop_lag", lag_ms)
```

**为何用 `sleep` 差值而不是 `call_soon` 往返**：`sleep` 差值直接反映「事件循环多久
没能按时处理定时器」，正是「被同步代码钉住」的度量，且实现简单、无额外线程。

**为何不依赖执行队列**：探针是 asyncio 任务，只在事件循环上运行，不需要工作线程，
也不取数据库连接——池满时它仍能被调度（池满阻塞的是**工作线程**，不是事件循环）。

**关键区分**：若事件循环被阻塞，`event_loop_lag` 会变大；若只是池满，
`db_pool_wait` 变大而 `event_loop_lag` 不变。这正是上一任务缺的判据。

### 1.3 阶段归属

两个指标独立记录，**不合并**成「响应慢」。日志事件名分别是
`db_pool_wait` 与 `event_loop_lag`，不出现 `threadpool_exhausted` 这类推测性命名。

## 2. 配置

```python
DB_POOL_WAIT_WARN_MS       # 默认 1000，超过记告警
EVENT_LOOP_LAG_WARN_MS     # 默认 500，超过记告警
EVENT_LOOP_LAG_INTERVAL_S  # 默认 1.0
DIAGNOSTICS_ENABLED        # 默认 true
```

在 `config.ensure_ready` 校验边界。新增配置项由本需求直接证明（R0 要求可观测），
不是推测性配置。

## 3. 生命周期

- 池等待包装在 `app/db.py` 模块加载时生效（engine 创建处），**始终启用**——
  它只是一次 `perf_counter()` 差值与阈值比较，开销可忽略；关闭它会让缺陷再次不可见。
- 事件循环探针随维护循环启动（`maintenance_loop` 内创建任务），随其取消而退出，
  不新增生命周期持有者。

## 4. 安全

- 日志字段：`waited_ms`、`checkedout`、`size`、`overflow`、`lag_ms`、
  可选 `route_template`（路由模板，非完整路径）与 `run_id`。
- **不得**含：SQL 语句或参数、请求体、token、凭据、PII。
- 复用现有 `logctx` 结构化日志与 request_id。

## 5. 验证

- 单元：池满时 `engine.connect()` 产生 `db_pool_wait` 告警且 `waited_ms ≈ pool_timeout`；
  事件循环被同步 sleep 阻塞时产生 `event_loop_lag` 告警。
- 区分性：仅池满（事件循环空闲）时**只有** `db_pool_wait`，不产生 `event_loop_lag`。
  这条断言是 R0「阶段归属」的核心，必须能区分两种缺陷。
- 真实负载：用该诊断定位池尖峰持有者（AC-1）。
- 变异验证：删掉阈值判断 → 用例失败。

## 6. 风险

- 池等待包装若在热路径上引入锁或分配，会成为新的串行点。实现只用
  `perf_counter()` + 整数比较，无锁、无分配（阈值以下不构造日志参数）。
- 事件循环探针本身占用一个定时器；间隔 1s、单任务，开销可忽略。
- 若 AC-1 定位到持有者需要改动 provider/steward 事务边界，必须回归
  fence/幂等/结算合同。
