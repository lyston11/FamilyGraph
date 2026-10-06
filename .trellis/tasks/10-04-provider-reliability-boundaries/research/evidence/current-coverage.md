# Provider 可靠性任务：当前覆盖核查（2026-10-06）

## 结论

**run 级重试预算已实现**（`10-03-agent-resource-isolation` 交付），
但**长流并发、backpressure、连接生命周期与 circuit breaker 未实现**。
因此本任务的真实工作集中在 stream-level，而不是重试次数。

## 已覆盖

### run 级重试预算（`agent/src/retry-budget.ts`）

- 两层重试（请求层 `providerStreamMaxRetries` × 会话层 `SESSION_RETRY_BUDGET`）
  相乘的问题，由**传输层** `wrapFetch` 统一封顶——这是两层唯一共同必经点；
- `AGENT_RUN_MAX_PROVIDER_ATTEMPTS`（默认 8）、`AGENT_RUN_MAX_TOTAL_RETRY_MS`（默认 120000）；
- 预算耗尽后**不再打开 socket**；
- **拒绝形态是行为契约**：必须返回合成的永久 4xx（`RUN_RETRY_BUDGET_HTTP_STATUS = 400`）
  + 网关同形脱敏 envelope，否则 OpenAI SDK 会包装成可重试错误，两层继续退避；
- 生产依据（开发库实测）：失败 run 出站尝试数 p50=p90=p99=**24**（6×4 上界），
  成功 run p50=3 / p90=7 / p99=11 / max=17；**默认 8 会截断 6% 的成功 run**（刻意取舍）。

回归：`agent/test/retry-budget.test.ts`、`retry-governance.test.ts`（mutation 验证）。

### egress 审计与错误分类

`provider_proxy.py` 对**每次真实出站尝试**写恰好一条 `agent_provider_egress`，
detail 含 `error_class` / `retryable` / `sent`（`sent=false` 仅由连接未建立证据得出）。

## 未实现（本任务的核心）

| 项 | 现状 |
|---|---|
| **stream-level 并发上限** | 无。admission 只覆盖**建连阶段**，流式转发前归还名额——因此一个租户可同时持有多个已建立的长流（代码注释已声明「已知未覆盖」） |
| **wall-clock deadline** | 无独立流级 deadline（仅 plan/lease 层面的时限） |
| **backpressure** | 未处理上游 SSE 快于消费端的情况 |
| **circuit breaker** | 无。上游持续失败时只有 run 级预算兜底，没有按 provider profile/kind/tenant 的熔断 |
| **连接生命周期** | 每请求新建 `AsyncClient`（实测节省约 49.5ms/请求，属核心生命周期改动，留档未实施） |

### 为什么 stream-level 是真实缺口

`10-03-agent-resource-isolation` 的实现明确记录：

> **已知未覆盖**：上游**并发流数**不受本层限制，一个租户仍可同时持有多个已建立的上游流；
> 限制它需要流级配额，属于后续阶段。

这与 `09-30` 的实测一致：长流曾把请求级 Session 跨 chunk 持连接，导致池被钉住。
当前已改为每 chunk 用独立短 Session，但**流本身的并发数仍无上限**。

## 与其它任务的关系

- **run 级重试预算**：已交付，本任务不改其数值；
- **control-plane 保留容量**：归 `10-04-control-plane-fault-domain`；
- **circuit hint 的存储**：Redis 只可缓存 hint，**不得**成为 circuit 真源
  （归 `10-03-redis-coordination`）；
- **DERP/Tailscale 上游可达性**：属部署/网络层，不是应用 circuit 能修复的，
  但 circuit 应能在其不可达时有界失败而不是放大重试。

## 因此本任务的实际工作

1. **stream-level 配额**（global / provider-kind / tenant）+ 有界 deadline；
2. **backpressure** 与 SSE 消费端慢速处理；
3. **circuit breaker**（按 provider profile / kind / tenant 分区）；
4. **连接生命周期**（persistent client）——需先基准；
5. 上述各项的**故障注入**（DERP、DNS、connect、header、stream 中断）。

**不重复实现** run 级重试预算。
