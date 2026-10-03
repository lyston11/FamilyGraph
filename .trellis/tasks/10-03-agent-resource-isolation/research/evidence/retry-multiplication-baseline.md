# 两层重试乘法与 run 级预算基线（2026-10-03）

## 生产证据（开发库）

对 `agent_runs` × `agent_provider_egress` 按 run 统计真实出站尝试数：

| 种群 | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| 成功 run | 435 | 3 | 7 | 11 | 17 |
| 失败 run | 197 | **24** | **24** | **24** | 25 |

失败 run 的中位数**恰好等于** `(5+1) × (3+1) = 24`，即请求层与会话层的上界乘积。
成功 run 中 27/435（6%）超过 8 次。

## 机制（对真实 SDK 0.84.3 验证）

- 层 1 `retryProviderRequest`：只重试带 `status`+`headers` 的 provider error，且仅
  408/409/429/5xx（或 `x-should-retry: true`）。
- 层 2 `isRetryableAssistantError`：按**错误文本**匹配
  `RETRYABLE_PROVIDER_ERROR_PATTERN`（overloaded/rate limit/429/5xx/timeout/
  connection error/...），并对 overflow 模式另判。
- 两层各自配置、互不可见，因此上界相乘。

## 关键实验：拒绝形态决定是否真的停止

| 拒绝方式 | 尝试数 | 结果 |
|---|---|---|
| `throw new Error(sentinel)` | 正确封顶 | 仍烧约 60s 退避才失败（SDK 包装为 `APIConnectionError("Connection error.")`，文本匹配暂时性模式） |
| 合成永久 4xx + 脱敏 envelope | 正确封顶 | 立即停止，两层都不重试 |

第一版实现采用 throw，`agent/test/retry-budget.test.ts` 的两个真实 SDK 用例
在 30s 超时；改为 4xx 响应后 8/8 通过。这记录了一个容易误判的点：
「尝试数正确」不等于「重试已停止」。

## 未覆盖范围

- 账户/空间级配额、公平队列、控制面独立 worker 池、sidecar 分进程尚未实现。
- 本基线只证明 run 级封顶；跨租户公平性仍待 Phase B/E。
