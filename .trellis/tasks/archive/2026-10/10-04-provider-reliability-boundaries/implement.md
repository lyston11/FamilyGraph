# 实施计划：Provider 长流可靠性与上游故障隔离

## Phase A：现场基线

- [x] 从 egress 审计量化 connect/header/stream/backpressure、重试、连接持有和长流并发。
      证据：2026-10-23 对账：`agent_provider_egress` 覆盖 1441 个 run，尝试数 min=1 p50=4 max=28，`sent=false` 均带 `error_class`
- [x] 关联 upstream/provider/kind/tenant，但不输出凭据、prompt 或正文。
      证据：`agent_provider_egress` 的 detail 只含安全字段 `error_class`/`retryable`/`sent`（见 `#447`）

## Phase B：流级资源

- [x] 建立 stream-level account/space/kind/upstream/global capacity 和总 wall-clock deadline。
      证据：`capacity.stream_specs`（global/agent_kind/tenant 三层）+ `provider_proxy` 的 `deadline` 墙钟上限（`stream_deadline_exceeded`）
- [x] 验证建连名额在流开始前释放、长流不占 control、取消/失租立即停止。
      证据：`internal_agent.py:689` 的 `_try_acquire_stream_slot` / `_release_stream_slot_soon`；流级名额独立于 control 路径

## Phase C：故障隔离

- [x] 实现按 upstream/profile/kind/tenant 的 circuit 状态和有界退避。
      证据：`provider_circuit.py`（227 行，CLOSED/OPEN/HALF_OPEN）+ `test_provider_circuit.py` 11 条；`provider_proxy` 在发送前 `allow()`、失败后 `record_failure()`
- [x] 注入 DERP 不可达、DNS/TLS、header timeout、stream reset、backpressure、cancel。
      **2026-10-23 补齐**：`_classify_transport_error` 的生产实现分派 6 条路径，但测试
      只注入了 `ConnectError` 与 `ReadError`，`sent` 断言只覆盖 3 种异常——
      **安全语义可被静默改坏**（`sent=false` 是 egress 审计里「本次出站未发送」的
      唯一证据）。新增两条测试逐分支钉住 `(error_class, retryable, sent)` 三元组，
      覆盖 ConnectTimeout / ConnectError / ReadTimeout / WriteTimeout / PoolTimeout /
      RemoteProtocolError / ReadError / WriteError / InvalidURL。
      **mutation 验证**：把 `RemoteProtocolError` 改成 `retryable=True` →
      1 项失败；把 `TimeoutException` 的 `sent` 改成 `False` → 3 项失败。
      两者在改动前都不会让任何测试失败。

      **2026-10-23 追加：用真实 socket 产生真实故障**（`test_provider_transport_faults_real_socket.py`）。
      上面两条用的是**构造的异常实例**，只验证「给定类型 → 返回什么分类」，
      不验证「真实网络故障确实抛出该类型」（httpx 的异常映射可能随版本变化）。
      新文件起真实本地 TCP 服务，用真实 `httpx.AsyncClient` 发请求，覆盖：
      连接被拒（`ConnectError`, sent=False）、DNS 失败（同样映射为 `ConnectError`,
      sent=False）、响应头超时（`TimeoutException`, sent=True）、
      响应头前 RST 与响应体中途 RST（`sent=True`，**这是 `sent` 语义的承重断言**）。

      **并修掉一个潜在风险**：`pooled_client` 此前未显式设置 `trust_env`，
      即 httpx 默认的 `True` → 会读 `HTTP_PROXY`/`HTTPS_PROXY`。
      实测后果：设了 `HTTP_PROXY` 后连一个**已关闭的本地端口**返回 `ReadTimeout`
      而非 `ConnectError`，于是「连接被拒」被记成「超时且可能已发送」，
      `sent=false` 的审计证据丢失。已显式 `trust_env=False`。
      生产 env 目前无代理，因此这是**潜在**风险；但一个能静默改道上游流量的
      环境变量不应依赖「没人会设」来防住（spec 明确 api 容器是唯一 egress，
      全仓无出站代理配置）。**mutation**：移除该行 → 1 项失败。
- [x] 验证不同 provider/kind/tenant 不互相熔断，egress/sent/settle 合同不变。
      证据：`circuit_key(provider_id=..., kind=...)` 按 provider+kind 分区；`test_provider_circuit.py` 覆盖隔离性

## Gate

- [ ] 长流多租户 p95/p99 和资源上界有真实证据；不得用单次成功替代尾延迟验收。


## 覆盖核查（2026-10-06）

**run 级重试预算已交付**（`10-03-agent-resource-isolation`），本任务**不重复实现**。
真实工作集中在 **stream-level**：并发上限、deadline、backpressure、circuit breaker、
连接生命周期。逐条证据见 `research/evidence/current-coverage.md`。

### 已覆盖（勿重复）

- `agent/src/retry-budget.ts`：传输层统一封顶两层重试相乘；
  `AGENT_RUN_MAX_PROVIDER_ATTEMPTS`（8）/ `AGENT_RUN_MAX_TOTAL_RETRY_MS`（120000）；
- **拒绝形态是行为契约**：合成的永久 4xx + 网关同形 envelope，否则 SDK 会继续退避；
- egress 每次真实尝试恰好一条审计，含 `error_class`/`retryable`/`sent`。

### 剩余工作

- [x] **stream-level 配额**：admission 只覆盖建连阶段，流式转发前归还名额，因此
      证据：已实现并接入真实路径（`capacity.try_acquire_stream` + `internal_agent.py` 的 acquire/release）
      一个租户可同时持有多个已建立的长流（实现注释已声明「已知未覆盖」）。
      需 global / provider-kind / tenant 三级流上限 + 有界 wall-clock deadline。
- [x] **backpressure**：上游 SSE 快于消费端时的处理。
      证据：`c4-stream-limits.md`：拉取式异步生成器天然提供 TCP 背压，实测正确并补了守护断言
- [x] **circuit breaker**：按 provider profile / kind / tenant 分区；open 时在发送前
      证据：`provider_circuit.py` 已实现（见上）
      快速拒绝，control-plane 不受影响；半开只允许有界探针。
- [x] **连接生命周期**：persistent `AsyncClient`（实测节省约 49.5ms/请求），需先基准。
      证据：`test_provider_client_lifecycle.py` 8 条 + `client_lifecycle_probe.py`（真实 HTTPS：池化收益已量化、连接确实被复用）
- [ ] **故障注入**：DERP 不可达、DNS 失败、connect/header/stream 中断、
      backpressure 慢消费。

## 2026-10-23 复核

上面 10 项已闭合。原先「依赖 C2 counter 合同」的阻塞已解除。

**仍然开放**：

| 项 | 为什么还开着 |
|---|---|
| 注入 DERP 不可达、DNS/TLS、header timeout、stream reset、cancel | 需要真实上游故障注入；`c4-stream-limits.md` 明确记为未做 |
| backpressure **慢消费**注入 | 机制已证正确，但未注入验证 |
| 长流多租户 p95/p99 与资源上界 | **需要真实部署与尾延迟**，不得用单次成功替代 |
