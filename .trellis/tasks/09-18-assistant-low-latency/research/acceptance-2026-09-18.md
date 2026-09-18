# 助手低延迟：LL-AC1～AC6 验收证据

测量时间：2026-09-18 18:30–20:20 CST。源码：主检出 `main@802925f`，远端 `familygraph-api`
（09:48:03 UTC 启动）与 `familygraph-agent`（10:07:03 UTC 启动，`dist/` 同刻重建）均运行该版本。
主会话内联执行，无子智能体。所有 run id 指向远端生产库 `agent_runs`，读取经只读副本查询，未写库。

## 环境与配置来源（先核实，不按默认值推断）

| 项 | 实测 | 依据 |
| --- | --- | --- |
| 前端 | 本地 vite `localhost:5173`，cwd `/Users/lyston/PycharmProjects/familygraph/frontend`，含 `assistant.text_delta` 处理 | `lsof -p <vite> -a -d cwd`；`grep -c text_delta frontend/src/stores/agent.ts` = 3 |
| 本地 8000/8001/8002 | launchd SSH 隧道（`ssh` 进程），不是本地后端 | `lsof -nP -iTCP:8000 -sTCP:LISTEN` |
| 模型档位 | 平台未设档位，SDK 回退 `medium`（09-17 已记） | 未在本轮改动 |
| Provider | `liu-dada` / `gpt-5.6-sol`，`api=openai-responses`，1 条记录 | 远端 `agent_providers`（凭据列未读取） |
| 请求层预算 | 次数不变（请求层 5 + 会话层 3），首响应期限 20s，暂时错误退避 500ms | E-R5 决议，代码 `provider_proxy.py:67/425` |

## LL-AC1：每个 run 有版本、配置来源、场景、阶段值与终态

测量口径：`agent_run_events` 的 `assistant.text_delta`（SDK 首正文分片落库）与
`message.assistant_added.timing_json`（sidecar 源计时，含 `first_text_ms` / `duration_ms`），
`agent_runs.first_leased_at`（不可变首次 lease），`agent_provider_egress` 审计（`target_id = run_id`）。
不把 `created_at` 当执行时间；源 monotonic 与跨进程 UTC 不直接相减（本报告浏览器数值单列）。

短问答（无工具轮）同 prompt 重复 3 次的配对样本，用于区分「是否提示冷/热」：

| prompt | run（header_ms 顺序） |
| --- | --- |
| 你好呀 | 12（6635）→ 14（1624）→ 20（1365） |
| 哈喽 | 15（1466）→ 21（1162） |
| 辛苦啦 | 16（1837）→ 22（1983） |
| 早上好 | 17（1292）→ 23（1371） |
| 好的 | 18（1032）→ 24（1405） |
| 明白了 | 19（1110）→ 25（1227） |
| 在吗 | 11（85，失败）→ 13（2041） |

→ 首次出现即已是低值（run 14 的 1624 晚于 run 12 的 6635，之后稳定在 1.1–2.0s），
**不是「提示缓存首次未命中被后续掩盖」所致**；但本轮数据**不能证明 cache hit**（见 LL-AC2 判断）。

## LL-AC2：无重试首发成功的同条件前后对照

**前置事实**：修复前不存在任何 `assistant.text_delta`（run 1/2，2026-09-15）；正文只在整条消息结束时
公开。修复后每个成功 run 都有分片。

成功 run 汇总（n=17，均为无重试首发成功，除 run 10/12 各 1 次为上一 run 失败后的新 run）：

| 统计量 | `first_text_ms` | `duration_ms` |
| --- | --- | --- |
| 中位数 | **1977** | **2618** |
| 最小 | 1165 | 1595 |
| 最大 | 7844（run 10，异常离群，见下） | 8135 |

**用户体验目标判定（3s 首段 / 8s 完整，n=17）**：

| 目标 | 达成 | 未达成 |
| --- | --- | --- |
| 首段 ≤ 3s | 16/17（94%） | run 10（7844，`header_ms=7718`，上游自身慢） |
| 完整 ≤ 8s | 17/17（100%） | — |

**明确减少的阶段**：`duration_ms − first_text_ms` = 「正文已生成但用户在页面上看不到」的旧等待，
实测 **151 / 291 / 491 ms**（短问答），即该段从「等于整条消息生成时长」降为数百毫秒。

**未达强声明的不做宣称**：

- 短问答首段**中位数** 1977ms 在 3s 内，但 p95/max 受上游首字节支配（`header_ms` 实测
  1032–7844ms），**不能宣布「3s 必达」**；真实 Provider 无此保证，需更多样本。
- 浏览器实测首段（同一浏览器 `performance.now()` 基线）在 2328–4689ms 区间，且**不与 run id 配对**
  （探针不携带 run id，首次对齐尝试已出现矛盾，见 [browser-acceptance](browser-acceptance-2026-09-18.md)
  的「批次对齐限制」）；本地链路比生产直连多一跳，故不与其相减。
- **不宣布「cache 已生效」**：重复 prompt 无「首次高、后续低」的单调模式（run 12 一次高值即被 run 14
  压低），且仓库内无 usage/cacheRead 落库字段可核验，故 P1-1 的收益**保持未实测**，只登记实现与回归。

## LL-AC3：真实 SDK + 假网关的重试/取消/压缩行为

由 E 承载并已集成（`4a850d1` → main `e0ee321`），本任务未改预算。回归入口与结果见
`09-17-assistant-retry-governance/evidence.md` §6/§8；agent 全量 163 tests 与 `retry-governance.test.ts`
本轮复跑通过。本轮**未实施**任何降次方案。

## LL-AC4：增量显示的浏览器证据

完整记录见 [browser-acceptance-2026-09-18.md](browser-acceptance-2026-09-18.md)。要点：

- **完整答案前看到安全正文**：浏览器侧在 2328–4689ms 观测到首个非空临时正文且有分片递进；
  注意探针在部分批次未观测到（SDK 分片与权威消息同窗口落库），未观测不等于用户没看到。
- **最终文本一致**：run 7/8/9/10/12 的分片拼接与权威 `message.assistant_added.text` **逐字节相等**
  （99/123/20/4/25 字符）。
- **重连不重、不串主体**：刷新页面并重新打开助手后 `[data-test="provisional-mark"]` 计数 = 0，
  只出现一条权威 assistant 消息；临时事件不物化 `AgentMessage`（session 8 仅 4 行 user/assistant 成对）。
- **失败不伪装成功**：run 6/11 在 `run.failed` 后前端显示 `PROVIDER_STREAM_ERROR` 文案，不保留临时正文为答案。

## LL-AC5：累计回归与部署状态

| 包 | 命令 | 结果 |
| --- | --- | --- |
| agent | `npm run lint && npm run type-check && npm test && npm run build` | 163 passed，lint/type-check/build 通过 |
| backend | `ruff check . && ruff format --check . && mypy app && pytest -q` | 全绿；**1705 passed / 3 skipped** |
| frontend | `npm run lint && npm run type-check && npm test` | 762 passed，lint/type-check 通过 |

部署状态（独立核实，不按「已合入 main」推断）：远端 `familygraph-api` 与 `familygraph-agent`
的 `ExecMainStartTimestamp` 为 2026-09-18 09:48:03 / 10:07:03 UTC，晚于 `main@802925f` 的提交时间；
`backend/app/services/agent_events.py` 含 `PROVISIONAL_TEXT_EVENT_TYPES`（2 处），
`agent/dist/events.js` 含 `assistant.text_delta`（4 处），`dist/session.js` 含
`fg-${projection.account_id}-${projection.session_id}`。真实浏览器链路已跑通（本节与 AC4）。

## LL-AC6：真实样本与未知项

- 全部样本为真实 Provider 真实 SSE 真实浏览器；未使用受控假模型产生「变快」结论。
- **未做**费用对照：两侧 model cost 置零，仓库内无计价来源，不编造金额。
- 样本量 n=17（短问答 16 + 工具轮 1），**不足以宣称稳定 p95**。
- 观察到的两条独立缺陷（不在本任务范围，未修）：上游对部分输入返回 422（run 6/11）；
  `list_visible_people` 首次调用被后端 `AGENT_TOOL_SCHEMA_INVALID` 拒绝后模型换参重试成功
  （run 3/7/8）。后者只记「额外字段」不记字段值，无法从审计定位具体键。

## 与 implement.md 中 P2 项的对照（结论：不做）

| 项 | 计划估算 | 实测 | 结论 |
| --- | --- | --- | --- |
| P2-1 减少 per-chunk DB 事务 | ~200–500ms | `_refresh_run_gate` 在真实库上 **median 368µs / p95 409µs**（300 次，远端 `.venv`）；每 run 分片数 1–8，即每 run 总开销 < 5ms | **不做**。改动会把取消/失租安全复核从逐 chunk 降为仅流首尾，破坏 `test_proxy_audits_cancellation_during_stream_once` 锁定的「流中取消立即停止转发」合同；用安全的取消语义换 <5ms 不成立 |
| P2-2 复用 httpx client | ~100–500ms | 真实上游实测节省 **≈49.5ms/请求**（服务端 fresh 59ms → 复用 10ms，各 6 次）；本批 run 最多 6 次请求 ≈ 0.3s | **本轮不做**。收益真实但小于 1.6–3.2s 的 `duration_ms` 的 2–10%，且生命周期/并发/关闭语义属网关核心改动（E 与 F 正在改同一文件的流式路径），不在本任务收尾内夹带。数据已留档，可另立实现 |
