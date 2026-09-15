# 助手链路优化：空事件过滤与会话压缩

> 父任务：09-15-agent-audit-remediation。优先级 P2。

## 背景

run #2 时间线显示：首个 turn 模型只发出工具调用（无正文），却产生了一条 `text=""` 的 `message.assistant_added` 事件；会话历史每轮全量重放。

核查后 **R2 的前提被证伪**，详见 `design.md` §0 的实测证据：

- Pi 的自动压缩**已经启用并已接线**：`session.ts` 用无参 `SettingsManager.inMemory()`（默认 `enabled=true, reserveTokens=16384, keepRecentTokens=20000`），压缩源是预填过的 `SessionManager`；`test/session-history.test.ts` 已覆盖「自动阈值压缩」与「零 usage 历史恢复后压缩」，`worker.integration.test.ts` 已覆盖「overflow → 压缩 → 重试成功」。
- 已归档 spec 明确要求保留该默认压缩，并把「关闭自动压缩 / recent-N 截断」列为**错误做法**（`assistant-history-restoration.md` §3、§5）。再叠一层投影层摘要会与之冲突，且截断中段历史正是 `internal_agent.py` 注释警告的「让模型自相矛盾」。
- 实测 33s 延迟在模型生成（上游中转），不在本地压缩。

R1 则是**真实缺陷，且影响比原描述更严重**：前端 `MessageList.vue` 的进行中指示是
`runActive && !messages.some(m => m.role === 'assistant')`。工具 turn 的空 assistant 消息一落地，
指示就在 **+5.2s 消失**，而 run 要到 **+38.4s** 才结束——用户面对一个空气泡、且整整 33 秒没有任何进行中反馈。
空消息同时被持久化成 `agent_messages` 行（生产库 id=5），并在后续 run 中作为空 assistant 历史重放给模型。

## Requirements

- R1：**过滤工具 turn 的空 assistant 公开事件**。sidecar `events.ts` 的 `message_end` 分支：assistant 文本为空**且**该消息 content 含 `toolCall` 块时，不产生 `message.assistant_added`。
  - 工具调用本身仍由既有 `tool.execution.started/completed` 事件如实上报，不丢失可观测性。
  - 内部 turn 计数语义（`turn.started/completed`）不变。
  - 只过滤「空文本 + 有工具调用」；有正文的工具 turn、以及正文非空的最终回答一律照常上报。
- R2（**已按核查结论放弃**）：不新增投影层摘要压缩。理由见上；已归档 spec 已规定保留 SDK 默认自动压缩。
- R3：确认过滤后前端无空消息闪现，且**进行中指示在整个 run 期间保持可见**（含工具 turn 阶段）。
- R4：测试：`mapSessionEvent` 空事件过滤单测（正/反例）；工具 turn 期间指示保持可见的前端回归；既有 agent/frontend 测试不回归。

## Acceptance Criteria

1. `cd agent && npm run lint && npm run type-check && npm test && npm run build` 全绿。
2. `cd frontend && npm run lint && npm run type-check && npm test && npm run build` 全绿。
3. 新 run 时间线中，工具 turn 不再出现 `text=""` 的 `message.assistant_added`；最终回答事件照常存在且正文完整。
4. 后端零改动（`agent_events.py` 因无事件而不落空消息行，无需改动）。
5. 部署后远端新会话：无空 assistant 消息行；引用补取（`/events/{seq}/citations`）与既有引用链路不回归。

## 设计取舍说明

- 过滤点选 `mapSessionEvent`（纯函数、既有单测入口），不选 `RunEventBuffer.onSessionEvent` 或后端：后端本就不该收到空事件，在源头过滤最小且可单测。
- 判据用「content 含 `toolCall` 块」而非仅 `stopReason === "toolUse"`：直接表达「这条消息只承载工具调用、没有正文」，不依赖各 provider 的 stopReason 命名。
- 不处理「最终回答为空」的独立缺陷（正文空且无工具调用）：那是 provider 返回空答案的另一类问题，语义与处置都不同，不在本任务范围。

## 回滚

`mapSessionEvent` 单点 revert；无 schema 变更、无迁移、无数据清理需求（已落库的历史空消息无害，不回改）。

## 后续项（本任务不做，写入触发条件）

- **中文 token 估算校准**：Pi 的 `estimateTokens` 用 `chars/4`，实测中文 520 字估 130 token（真实 tokenizer 约 1 字 1 token），中文会话下压缩触发约晚 4 倍。当前无生产证据（会话最长 6 条消息），且 overflow 自动恢复已覆盖兜底（`_checkCompaction` 的 overflow 分支 + 重试，已有测试）。触发条件：真实会话出现 overflow 恢复或单会话消息数显著增长时，评估自定义 `compaction.reserveTokens`。
- **空最终回答**：正文空且无工具调用的 turn 会产出空气泡，属独立缺陷，需单独设计与验证。
