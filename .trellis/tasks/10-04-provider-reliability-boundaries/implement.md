# 实施计划：Provider 长流可靠性与上游故障隔离

## Phase A：现场基线

- [ ] 从 egress 审计量化 connect/header/stream/backpressure、重试、连接持有和长流并发。
- [ ] 关联 upstream/provider/kind/tenant，但不输出凭据、prompt 或正文。

## Phase B：流级资源

- [ ] 建立 stream-level account/space/kind/upstream/global capacity 和总 wall-clock deadline。
- [ ] 验证建连名额在流开始前释放、长流不占 control、取消/失租立即停止。

## Phase C：故障隔离

- [ ] 实现按 upstream/profile/kind/tenant 的 circuit 状态和有界退避。
- [ ] 注入 DERP 不可达、DNS/TLS、header timeout、stream reset、backpressure、cancel。
- [ ] 验证不同 provider/kind/tenant 不互相熔断，egress/sent/settle 合同不变。

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

- [ ] **stream-level 配额**：admission 只覆盖建连阶段，流式转发前归还名额，因此
      一个租户可同时持有多个已建立的长流（实现注释已声明「已知未覆盖」）。
      需 global / provider-kind / tenant 三级流上限 + 有界 wall-clock deadline。
- [ ] **backpressure**：上游 SSE 快于消费端时的处理。
- [ ] **circuit breaker**：按 provider profile / kind / tenant 分区；open 时在发送前
      快速拒绝，control-plane 不受影响；半开只允许有界探针。
- [ ] **连接生命周期**：persistent `AsyncClient`（实测节省约 49.5ms/请求），需先基准。
- [ ] **故障注入**：DERP 不可达、DNS 失败、connect/header/stream 中断、
      backpressure 慢消费。
