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

> **本表已过时（2026-10-10 修正）。** 下面五项中的四项实际**已经实现并有测试**，
> 该表写的是更早的状态。修正后的逐项状态见本节末尾的「修正后状态」。
> 保留原文是为了说明「覆盖证据会随实现漂移」——本任务曾因这份表被误判为
> 「被阻塞」，实际只有一项真正未做。

| 项 | 原表声称的现状 |
|---|---|
| **stream-level 并发上限** | 无。admission 只覆盖**建连阶段**，流式转发前归还名额——因此一个租户可同时持有多个已建立的长流（代码注释已声明「已知未覆盖」） |
| **wall-clock deadline** | 无独立流级 deadline（仅 plan/lease 层面的时限） |
| **backpressure** | 未处理上游 SSE 快于消费端的情况 |
| **circuit breaker** | 无。上游持续失败时只有 run 级预算兜底，没有按 provider profile/kind/tenant 的熔断 |
| **连接生命周期** | 每请求新建 `AsyncClient`（实测节省约 49.5ms/请求，属核心生命周期改动，留档未实施） |

### 修正后状态（2026-10-10 逐项核对）

| 项 | 真实状态 | 证据 |
|---|---|---|
| **stream-level 并发上限** | **已实现** | `capacity.stream_specs` 三层维度（global / agent_kind / tenant）；`internal_agent._try_acquire_stream_slot` 在流开始前取得、生成器 `finally` 里归还；`test_stream_capacity.py` 6 项 |
| **wall-clock deadline** | **已实现** | `config.AGENT_STREAM_MAX_DURATION_SECONDS`（默认 900）；`provider_proxy.passthrough_with_audit` 用专用异常 `StreamDeadlineExceeded` 中断并记 `stream_deadline_exceeded`；`test_stream_deadline_interrupts_and_audits_the_reason` |
| **backpressure** | **已实现** | 拉取式生成器（`async for chunk in upstream.aiter_raw()`），无无界缓冲；`test_stream_is_pull_based_so_a_slow_consumer_backpressures_the_upstream` 断言「上游推进量 ≤ 已消费量 + 1」 |
| **circuit breaker** | **已实现** | `provider_circuit.py`（closed/open/half_open + 有界探针 + 多次成功才闭合）；按 `provider_id × kind` 分区、**排除租户**（一个租户的失败不该熔断别人）；发送前快速拒绝且零出站；`test_provider_circuit.py` 11 项 |
| **连接生命周期** | **本轮实现（P3-c）** | `provider_proxy.pooled_client` 按 `base_url` 分池；实测节省 **51.1ms/请求**（p50 61.8ms → 10.7ms）；`test_provider_client_lifecycle.py` 8 项 |

**唯一真正未做的是连接生命周期，本轮已补。** 其余四项的「未实现」结论是文档漂移，
不是实现缺口。

### 为什么 stream-level 曾被认为是真实缺口（已不成立）

`10-03-agent-resource-isolation` 当时的实现记录：

> **已知未覆盖**：上游**并发流数**不受本层限制，一个租户仍可同时持有多个已建立的上游流；
> 限制它需要流级配额，属于后续阶段。

这条记录在**当时**是准确的。流级配额（`capacity.stream_specs` + `_try_acquire_stream_slot`）
后来已经落地，但这份证据文件没有同步更新，因此本任务被误判为「被阻塞」。

长流曾把请求级 Session 跨 chunk 持连接、导致池被钉住的问题也已修复
（每 chunk 用独立短 Session，见 `test_stream_does_not_pin_a_pool_connection_between_chunks`）。

## 与其它任务的关系

- **run 级重试预算**：已交付，本任务不改其数值；
- **control-plane 保留容量**：归 `10-04-control-plane-fault-domain`；
- **circuit hint 的存储**：Redis 只可缓存 hint，**不得**成为 circuit 真源
  （归 `10-03-redis-coordination`）；
- **DERP/Tailscale 上游可达性**：属部署/网络层，不是应用 circuit 能修复的，
  但 circuit 应能在其不可达时有界失败而不是放大重试。

## 因此本任务的实际工作（2026-10-10 修正）

1. ~~stream-level 配额 + 有界 deadline~~ —— **已实现**；
2. ~~backpressure~~ —— **已实现**；
3. ~~circuit breaker~~ —— **已实现**；
4. **连接生命周期（persistent client）** —— **本轮实现**，基准见
   `client_lifecycle_probe.py`；
5. **故障注入**（DERP、DNS、connect、header、stream 中断）—— 部分覆盖：
   `test_provider_proxy.py` 已覆盖连接失败、header 超时、流中断、取消、deadline、
   慢消费；**未覆盖**真实 DERP/Tailscale 网络层不可达（属部署层，应用侧 circuit
   只能保证有界失败）。

**不重复实现** run 级重试预算。
