# 验收证据（2026-10-02/03）

## 交付 1：plan 窗口按 attempt 数定尺（修正我上一轮的误判）

我上一轮把 `insufficient_budget` 归因为「token 预算不够（1 token/byte 估算）」。
**这是错的**：核对 job 预算后，剩余 10,876–18,696 tokens，而需要只有 305–7,125 —— 预算完全够。

真实原因是 **plan 墙钟 120s 太短**：

| plan | deadline | 前一个 run 结束 | 超期 |
|---|---|---|---|
| 1283 | 00:30:58 | 00:34:17 | **199s** |
| 1285 | 01:00:15 | 01:03:23 | **189s** |
| 1287–1299 | … | … | 190–200s |

每个被跳过的 attempt，其前一个 run 都在 deadline **之后约 190–200 秒**才结束。
实测 run 耗时 p50=142s、p90=320s、max=804s —— 120s 连**一个** run 都装不下。

**修复**：窗口 = `attempt 数 × STEWARD_ASSIST_ATTEMPT_WINDOW_SECONDS(600)`，
attempt 数与预留循环用同一个 fence 计算（ranking 组少于 2 张卡的被排除，与循环一致）。
覆盖率：旧 `attempts×120` 为 63%，`attempts×300` 为 89%，**`attempts×600` 为 100%**。

**部署后**：`insufficient_budget` **新增 0**；plan 窗口正确（2 attempt → 1200s，1 attempt → 600s）。

**附带修复**：迁移 0055 读取该 config key 回填 `deadline_at`，改名即崩。改为**历史字面量**——
迁移必须描述被迁移行当初的语义，读实时 config 会让它在未来默认值变更后产出不同结果。

## 交付 2：`invalid_output` 可诊断

`degraded/invalid_output` 只说明「未通过封闭校验」，不说明为什么——JSON 不可解析、
id 集合不符、字段越权是三种不同缺陷共用一个码。历史 15 次全部无法归因。

现在记录**结构性**事实（kind、文本长度、是否像 JSON、期望 id 数、attempt/run id），
模型原文不入日志。**部署后已产生 3 条诊断**，例如：

```
{"event":"assist_output_rejected","assist_kind":"ranking","text_chars":414,"looks_like_json":false}
{"event":"assist_output_rejected","assist_kind":"ranking","text_chars":680,"looks_like_json":true}
```

首次即可区分「不是 JSON」与「是 JSON 但结构不符」。

## 验证

- backend **1960 passed / 3 skipped**；ruff、mypy 干净
- 回归断言「窗口 ≥ attempt 数 × 单次窗口」而非具体秒数（对配置调整不敏感，
  只对「窗口与 attempt 数脱钩」敏感）；`invalid_output` 用例同时断言诊断存在
  **且原文不存在**，并在场景未产生该错误码时**显式失败**而非静默通过
- 变异验证 2 组：固定 120s 窗口 → 失败；移除诊断日志 → 失败

## 本轮附带发现（未修）

**重试风暴放大**：失败 run 每次产生 **24 条 egress** = 请求层 6 次
（`providerStreamMaxRetries=5`）× 会话层 4 次（`SESSION_RETRY_BUDGET.maxRetries=3`）。
一次 10 秒的不可达被放大成约 240 秒无效工作 + 144 条/小时审计噪声；成功 run 只用 1–5 条。

**billing 准确性**：`prompt_tokens`/`completion_tokens` 全为 NULL（近 3 天 283/283），
`total_tokens` 恒等于预留上限——每次成功调用都按**预留**而非**实际用量**计费。
属计费准确性问题，需要独立改动。

## 边界

- 线上未操作。
