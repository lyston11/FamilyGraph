# 真实浏览器增量验收（LL-AC4）实测记录

测量时间：2026-09-18 18:43–20:05 CST（10:43–12:05 UTC）。源码版本：主检出 `main@802925f`
（= 远端 backend 与 sidecar 正在运行的版本）。主会话内联执行，无子智能体。

## 环境（先核实再测量）

| 项 | 实测值 | 依据 |
| --- | --- | --- |
| 前端 | 本地 vite dev server `localhost:5173`，cwd = 主检出 `frontend/`，含 `assistant.text_delta` 处理 | `lsof -p 70188 -a -d cwd` → `/Users/lyston/PycharmProjects/familygraph/frontend`；`grep -c text_delta frontend/src/stores/agent.ts` = 3 |
| 后端/网关 | 远端 `familygraph-api`，`ExecMainStartTimestamp=2026-09-18 09:48:03 UTC`，加载 `802925f` | `systemctl --user show`；远端 `git log -1` |
| sidecar | 远端 `familygraph-agent`，`ExecMainStartTimestamp=2026-09-18 10:07:03 UTC`，`dist/` 同刻重建 | `stat -c %y dist/*.js`；`dist/events.js` 含 `assistant.text_delta`（4 处） |
| 本地 8000/8001/8002 | 是 launchd SSH 隧道（`ssh` 进程），不是本地 backend | `lsof -nP -iTCP:8000 -sTCP:LISTEN` |
| 模型 | `liu-dada` / `gpt-5.6-sol`，`api=openai-responses` | 远端 `agent_providers`（凭据列未读取） |

## 测量方法与其已知限制（先说清，免得把探针当权威）

浏览器侧用 `MutationObserver` + `performance.now()`：在提交（Enter）后观测
`[data-test="provisional-mark"]` 所在气泡的非空文本，取首次非空为「浏览器首段可见时刻」。
服务端侧用 `agent_run_events` 的 `assistant.text_delta` 落库时刻与
`message.assistant_added.timing_json.first_text_ms`（sidecar 源计时）。两者时钟不同源，**只分别报告，不做相减以外
的合成**。

**探针限制（实测到，不是假设）**：当 SDK 分片与权威 `message.assistant_added` 落在同一个 250ms drain 窗口内时，
临时气泡可能从未以「非空文本」状态被绘制，`MutationObserver` 便观测不到——本批 8 次提问中有 3 次如此
（含一次因为上一提问仍在运行时被拒）。因此**浏览器探针的未观测 ≠ 用户没看到**，不能拿它当反例，
也不能只报告观测到的那些。

**批次对齐限制**：浏览器探针不携带 run id，无法与 `agent_runs` 可靠配对。首次尝试按批次时间与 run 时间对齐后，
出现「探针记录时刻晚于该 run 的服务端结束时刻」的矛盾（例如探针记 9.54s 而同期 run 的 `duration_ms` 为 8135ms），
说明配对不可靠。**故本文件不再给出浏览器值 ↔ run id 的对应表**；两套数值分列。

## A. 浏览器侧实测（同一 `performance.now()` 基线，本地 vite + SSH 隧道）

| 提问 | 首个非空临时正文可见 | 首片长度 | 观测到最后一片 | 终态 |
| --- | --- | --- | --- | --- |
| 你好呀 | 4689 ms | 29 字 | 4689 ms | 权威替换，无残留 |
| 辛苦啦 | 3072 ms | 12 字 | 3396 ms（25 字） | 同上 |
| 早上好 | 3798 ms | 21 字 | 3798 ms | 同上 |
| 好的 | 2669 ms | 18 字 | 2907 ms（32 字） | 同上 |
| 明白了 | 2328 ms | 12 字 | 2328 ms | 同上 |
| 谢谢 / 哈喽 / 长辈问题 | 探针未观测到非空临时文本 | — | — | 答案正常出现 |

观测区间 2328–4689 ms，且**有多片递进**（如 12 → 25 字、18 → 32 字），即用户看到的是持续流入的正文，
不是等完整答案后一次性出现。本地链路比生产直连多一跳。

## B. 服务端侧实测（同一 run 的分片与权威文本对账）

对每个 run 取全部 `assistant.text_delta` 分片拼接，与 `message.assistant_added.text` 逐字节比较：

| run | 提问 | 分片数 | 拼接长度 | 权威长度 | 完全相等 |
| --- | --- | --- | --- | --- | --- |
| 7 | 这个空间里谁是我的长辈？ | 5 | 99 | 99 | 是 |
| 8 | 这个空间里谁是我的长辈？ | 8 | 123 | 123 | 是 |
| 9 | 你好 | 2 | 20 | 20 | 是 |
| 10 | 谢谢 | 1 | 4 | 4 | 是 |
| 12 | 你好呀 | 1 | 25 | 25 | 是 |
| 13 | 在吗 | 2 | — | — | 是 |
| 14 | 你好呀 | 3 | — | — | 是 |
| 15–25 | 短问答 ×11 | 1–3 | — | — | 是 |
| 26 | 这个空间里谁是我的长辈？ | 5 | — | — | 是 |

「拼接 = 权威」是**增量不篡改最终答案**的机械证明，与界面无关。

## C. 重连不重、不串主体

刷新页面并重新打开助手（会话 8、9 的重放路径）：`[data-test="provisional-mark"]` 计数 = **0**，
页面只出现一条权威 assistant 消息，正文与提问各一份，无重复、无残留临时气泡。机制上也成立：
临时事件不物化 `AgentMessage`（session 8 的 `agent_messages` 仅 4 行，user/assistant 成对），
重放路径只能重放权威消息。

## D. 失败不伪装成功

run 6（10:44:06）与 run 11（11:07:43）上游返回 `HTTP 422`，`retryable=false`，run 终态
`PROVIDER_STREAM_ERROR`；前端显示「模型服务暂时不可用，请稍后重试」，未把临时正文留在气泡里冒充答案
（两 run 的 `run.failed` 之后无 `message.assistant_added`）。

## 结论与边界

- **达成**：完整答案之前用户读到安全正文；分片拼接与权威文本逐字节一致；重连不重、不串主体；
  失败不被伪装成功。
- **未做声明**：不给浏览器值 ↔ run id 的配对；不宣布「3s 必达」（见
  [acceptance-2026-09-18.md](acceptance-2026-09-18.md) 的 n=17 统计）；不声称输出侧正文扫描存在
  （与完整消息同一缺口，按 design「明示未覆盖」分支交付）。
- 输出安全与「临时文本不入历史/Memory/RAG」由机制保证（`append_events` 只对
  `message.assistant_added` 建历史行），已在 `spec/backend/agent-runtime.md` 记录。
