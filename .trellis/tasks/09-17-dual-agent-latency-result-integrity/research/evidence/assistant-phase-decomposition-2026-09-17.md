# 助手延迟：受控复现与真实配置证据（2026-09-17）

基线 `main@62a2b15`（A 实施于 `076d631`）。本文件只记录可复核事实与复现方式，
不写正文、prompt、thinking、凭据或用户隐私。

## 1. 结论摘要

| 编号 | 结论 | 证据类型 | 状态 |
|---|---|---|---|
| E1 | 助手阶段可从既有持久事件拆解，无需新增字段 | 生产只读副本 + 代码 | 已证实 |
| E2 | 模型生成是主导阶段（逐轮 p50 33.15s） | 生产只读副本 n=2 run | 已证实 |
| E3 | 该「模型轮次」**含上游 5xx 重试与退避**，不是纯推理时间 | 生产只读副本 egress 审计 + 事件时间线 | 已证实 |
| E4 | 助手实际推理档位是 Pi SDK 默认 `medium`，平台无档位控制项 | 真实 Pi SDK + fake stream（无 egress） | 已证实 |
| E5 | 上游 `text_delta` 与公共 `message.assistant_added` 的差值是发布时机 | 真实 Pi SDK + fake stream | 已证实 |
| E6 | 档位对耗时的**影响幅度**、代理缓冲、时钟偏差仍未实测 | — | 未知 |

## 2. E1/E2：分段与主导阶段

真源为 `agent_run_events.created_at` 配合 `run.started`、`turn.started`、
`message.assistant_added`、`tool.execution.started/completed` 与 `agent_runs.settled_at`。
实现见 `backend/app/api/admin_agent_latency.py` 的 `assistant_phases`。

样本窗：生产库只读副本全部 assistant run（n=2）。缺失阶段记 `null`/`n=0`，不零填充。

run 2 时间线（真实数据，来自只读副本）：

```text
seq  0  message.user_added       07:54:16.341610
seq  1  run.started              07:54:17.282088   → 排队/取用 0.94s
seq  2  turn.started             07:54:17.282846
seq  3  message.assistant_added  07:54:21.575614   → 第 1 轮 4.29s（1 次请求成功，无重试）
seq  4  tool.execution.started   07:54:21.576305
seq  5  tool.execution.completed 07:54:21.576942   → 工具 0.6ms
seq  6  turn.completed           07:54:21.577503
seq  7  turn.started             07:54:21.578078
seq  8  message.assistant_added  07:54:54.729955   → 第 2 轮 33.15s（含 5 次 502 重试）
seq  9  turn.completed           07:54:54.730603
seq 10  run.settled              07:54:54.741122   → 落库→结算 10.5ms
```

逐轮 p50 33.15s；`queue_wait` p50 0.94s；`tool_call` p50/max 0ms；`settle` max 10ms。
**排队、工具与落库都不是主导**，与「助手慢在别处」的直觉相反。

## 3. E3：33.15s 中含 10.22s 重试退避（本轮新增，纠正 E2 的口径）

`agent_provider_egress` 审计（`audit_log.action='agent_provider_egress'`；`target_id` **就是 run id**，
`detail_json` 含 `provider_id`/`status`/`upstream_status`/`bytes_read`，无 prompt/响应正文）。
按 `target_id` 精确归属，run 2：

```text
07:54:21.138  succeeded  upstream=200  bytes=98229   ← 属第 1 轮
07:54:22.440  failed     upstream=502  bytes=0       ┐
07:54:24.098  failed     upstream=502  bytes=0       │
07:54:25.718  failed     upstream=502  bytes=0       │ 第 2 轮内
07:54:28.413  failed     upstream=502  bytes=0       │
07:54:32.660  failed     upstream=502  bytes=0       ┘
07:54:54.719  succeeded  upstream=200  bytes=33727   ← 第 2 轮重试成功
```

实测（只读副本上按同一算法复算）：连续失败段 `末次失败−首次失败` = **10.22s**。即：

```text
第 2 轮 33.15s ≈ 10.22s 上游 502 重试+退避（下界）+ 22.93s 其余（含纯生成）
```

**因此 `model_turn` 当前口径会把上游重试退避计入「模型生成」**，而 A-02 要求
「重试不误算为单次模型推理」。已实现 `provider_retry`（下界）+ `provider_failed_attempts`
（无歧义）两项把它单独标出，`model_turn` 口径本身保持原样（不篡改既有样本）。

**重要区分（run 1 vs run 2）**：重试形态不同，下界指标只能盖住一种。

- run 2：5 次 502 **集中在同一轮**（21.578→54.730），连续失败段长度 5，
  `provider_retry` 记 `末次失败−首次失败` = 32.660−22.440 = **10.22s**；
  该轮 `model_turn` 33.15s，余 22.93s 含纯生成（与首次失败尝试自身耗时）。
- run 1：两次 503 分属**不同轮**（第 1 轮 05:08:00.581→05:08:19.945，第 2 轮 05:08:53.767→05:09:02.946），
  每轮各一次失败 → 每段长度 1 → 按 `末次−首次` 定义均得 **0**。
  即 `provider_retry` 的 `n` **不反映** run 1 的两次重试；只能由
  `provider_failed_attempts=2` 看出有重试。这是下界指标的已知盲区。

重试配置（`agent/src/config.ts:107-108`，部署 env 未覆盖）：
`AGENT_PROVIDER_STREAM_MAX_RETRIES=5`、`AGENT_PROVIDER_STREAM_MAX_RETRY_DELAY_MS=20000`；
按既有合同对 5xx/408/409/429 指数退避。实测退避远小于 20s 上限，故上限不是瓶颈，
**触发源是上游 502/503 不稳定**。

观测缺口（如实记录）：

1. `agent_provider_egress` 有 run 级精确归属（`target_id`），但**没有轮次编号**，
   把同一 run 的多次重试拆到具体 turn 只能靠时间先后（本文件即如此；同 run 内串行时可靠）。
2. 审计只记请求完成时刻、不记开始时刻，因此**首次失败尝试的耗时不可知**，
   `provider_retry` 只能是下界。要得到精确的 per-attempt 重试耗时，需最小新增字段
   （A 设计 §3 步骤 5 允许的选项），本轮未实现。

## 4. E4：实际推理档位是 SDK 默认 `medium`

复现方式：真实 Pi SDK + fake stream，`fetch` 被拦截并断言未发生 egress
（与 `test/assistant-delta-gap.test.ts` 同一套 harness）。探针捕获 `streamSimple`
收到的 `options.reasoning`。

```text
THINKING_PROBE_OPTIONS_REASONING= ["medium"]
```

原因链（源码核对）：`agent/src/session.ts:414` 的 `createAgentSession` 不传 `thinkingLevel`；
SDK 在 `dist/core/sdk.js:115-135` 依次取 `options.thinkingLevel` → 每模型覆盖 →
`settingsManager.getDefaultThinkingLevel()` → `DEFAULT_THINKING_LEVEL`
（`dist/core/defaults.js:1` = `"medium"`）；sidecar 用 `SettingsManager.inMemory()`，
无默认档位设置，故落到 `medium`。

平台侧无档位控制项：`agent_providers.thinking_levels_json` 只声明该 Provider
**支持**的档位列表（生产为 `["low","medium","high","xhigh","max"]`），
`agent_space_provider_settings` 无档位列，assist 与 runtime 均无档位参数。

**这不是缺陷**，是未显式选择档位的默认行为；降低档位属质量取舍，须用户决定。

## 5. E5：delta → 可见的差值是发布时机

`agent/test/assistant-delta-gap.test.ts`（真实 Pi SDK + fake stream，无 egress）：
上游 3 个 `text_delta` 全部到达、SDK 也转发 `message_update`，但公共
`message.assistant_added` **恰好 1 次**且只在 `message_end`，内容为完整答案。
故「首段显示晚」= 剩余正文生成时间，与 SSE/渲染无关。该测试双向 pin：
若改为透传 delta 即失败。

## 6. 未实测（不得写成已定位根因）

**降低推理档位能省多少时间**（档位本身已测为 `medium`）、代理是否缓冲、
慢 chunk 是否真实发生、本地与服务器时钟偏差、并发 run 下的重试归属。

## 7. 复现命令

```bash
cd agent && npx vitest run test/assistant-delta-gap.test.ts   # E5，无 egress
cd backend && .venv/bin/python -m pytest -q tests/test_admin_agent_latency.py  # E1/E2 口径
```

E3/E4 为只读副本查询与一次性探针，非长期回归；口径已写入
`spec/backend/agent-runtime.md` 的「助手耗时按持久事件分段」小节。
