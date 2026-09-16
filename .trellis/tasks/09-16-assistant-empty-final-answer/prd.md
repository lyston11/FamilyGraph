# 助手空最终回答：虚假成功与空气泡

> 父任务：09-15-agent-audit-remediation（已归档）遗留项。优先级 P2。
> 来源：09-15 assistant-latency-optimizations 的 `agent-runtime.md` 明确把「正文空且无工具调用的空最终回答」列为**独立缺陷、另行处理**。

## 背景

模型返回**无正文的最终回答**时，系统对外宣称成功，但用户什么也没得到。

这条路径与已修复的工具 turn 空事件**不是同一个缺陷**：

| | 工具 turn 空消息 | 空最终回答（本任务） |
|---|---|---|
| 触发 | 模型调工具、无正文 | 模型结束回合、无正文 |
| 判据 | `stopReason === "toolUse"` | `stopReason === "stop"` |
| 状态 | 09-15 已修复（不产事件） | **仍产事件、仍结算 succeeded** |
| 用户感受 | 短暂无反馈（指示器提前熄灭） | **没有答案，也没有任何解释** |

### 核查结论（实测）

**生产目前没有本缺陷的实例**，需要如实说明：库里唯一那条 `text=""` 的 assistant 消息
（`agent_messages.id=5`，`run:2:event:3`）其 `seq=4/5` 有 `tool.execution.started/completed`，
即它是**工具 turn**，已被 09-15 修复。`run 1` 的 `seq=3` 有正文（`我先查询当前空间中…`），同属工具 turn。

**但路径可达，且三层都有证据**：

1. `agent/src/events.ts` 的 `mapSessionEvent` 显式保留该情形，`agent/test/events.test.ts:89`
   有一条测试 **`keeps an empty answer without tool calls (boundary of the filter)`** 正在固化这个行为
   （断言产出 `{text: ""}` 事件）。
2. `agent/src/worker.ts` 在 `prompt()` 返回后只检查「是否有 provider 错误」，
   **没有检查「是否真的产出了答案」**，随后无条件 `settleRun(..., "succeeded")`。
3. 后端 `_settle` 照单落库（`agent_events.py` 为 `message.assistant_added` 建 `AgentMessage` 行），
   前端 `MessageList.vue` 的 `{{ item.text }}` 无空值保护 → 渲染一个**可见空气泡**。

### 为什么比「空气泡」严重

- **虚假成功**：run 结算 `succeeded` → 前端 `finishRun(partition, 'succeeded')` 不设 `partition.error`
  → `ErrorNotice` 不渲染 → **用户得不到任何解释**（既没有答案，也没有失败提示）。
- **污染后续上下文**：空 assistant 行被持久化，并作为**空历史**重放进下一次 run 的 Pi 上下文
  （`internal_agent.py` 投影只要 `content_json["text"]` 是字符串就纳入，空串符合）。
- **遗留行永久可见**：`agent_messages.id=5` 已存在于生产库，会一直渲染为空气泡。

## Requirements

- **R1**：run 只有在**产出非空最终回答**时才允许结算 `succeeded`。最终回答指生成完成
  （`stopReason === "stop"` 或截断 `"length"`，两者都可能有正文）的 assistant 消息；
  工具 turn（`toolUse`）与中止（`aborted`）不参与判定。
  未产出时以新的 `PROVIDER_EMPTY_ANSWER` 结算 `failed`，用户得到明确解释。
  - 工具 turn 的正文**不得**冒充最终回答：判据取循环中**最后一条**符合上述条件的消息
    （工具 turn 的正文会被随后空的 `stop` 消息覆盖）。
  - provider 错误优先：已存在的 `PROVIDER_STREAM_ERROR` 判定顺序不变、语义不变。
  - 取消/租约丢失仍由服务端裁决，不得因此产生新的 `failed` 结算。
- **R2**：**不产出正文为空的 assistant 事件**。判据从「正文空 **且** 含 toolCall 块」
  收紧为「正文空」。空回答没有任何可展示内容，不应落库、不应作为空历史重放。
  - 这是 `agent-runtime.md` 已记录的延期项，本任务兑现。
  - **R1 与 R2 必须同批发布**：只做 R2 会把「可见空气泡」变成「静默成功」（用户连空气泡都看不到，
    也不知道发生了什么），比现状更糟。两者任一单独 revert 都需同时 revert 另一个。
- **R3**：前端不渲染空的 assistant 气泡（覆盖已存在的遗留行与滚动发布窗口内的旧 sidecar）。
  - **不得**因此丢弃该消息上的 `cardIds`/`citations`/`webCitations`（`frontend/action-card.md`
    允许助手消息携带结构化卡片引用，只隐藏空气泡本体）。
  - 用户消息气泡始终渲染（空正文的用户消息不是本任务的合法形态）。
- **R4**：`PROVIDER_EMPTY_ANSWER` 的错误文案进入前端映射表（与 `PROVIDER_STREAM_ERROR`
  同区块），遵循既有约定：**sidecar 运行期错误码不在 `backend/app/errors.py` 注册**，
  只在前端文案表 + spec 中登记。后端零改动。
- **R5**：不做「重发上一条消息」入口。失败后重试是独立的 UX 能力（需要草稿/重发语义），
  本任务只保证用户**得到解释**，不承诺一键重试。

## Acceptance Criteria

1. `cd agent && npm run lint && npm run type-check && npm test && npm run build` 全绿。
2. `cd frontend && npm run lint && npm run type-check && npm test && npm run build` 全绿。
3. 新增回归测试证明：模型只返回空最终回答（`stopReason: "stop"`、无正文、无工具调用）时，
   sidecar 结算 `failed` + `error_code: "PROVIDER_EMPTY_ANSWER"`，且**不产生** `message.assistant_added` 事件。
4. 既有行为不回归：`PROVIDER_STREAM_ERROR`（provider 错误）、工具 turn 过滤、
   压缩后重试成功的 `succeeded`（最终回答非空）逐字不变。
5. `PROVIDER_EMPTY_ANSWER` 经 `friendlyAgentError` 得到中文解释（非通用兜底文案）。
6. 前端不渲染 `text === ''` 的 assistant 气泡，但同一消息上的卡片引用仍渲染。
7. 后端零改动；`pytest` 受影响面通过。

## 明确不做

- 不改 `stopReason === "length"`（max_output_tokens 截断）的语义：截断通常仍有正文，
  且它属于「部分答案」而非「无答案」，需要单独设计。
- 不做仅空白正文（如 `"\n"`）的归一：按 `length > 0` 视为非空，与前端既有判据保持一致；
  生产 0 例，如需处理应在 sidecar 单点 trim（后续项）。
- 不改后端 settle 契约（不加「succeeded 必须有 assistant 事件」的服务端不变式）：
  运行结果由 sidecar 判定，后端复算需要回查事件，属重复真源。
- 不做一键重发/自动重试（R5）。
- 不清理生产库中已存在的遗留空行（`id=5`）：前端保护已使其不可见，
  数据清理需要独立的数据修复流程与审计。

## 回滚

三个 commit 各自独立可 revert：事件过滤、结算判定、前端渲染保护。
revert 后回到当前行为（空回答仍报成功、仍显示空气泡），无 schema 变更、无数据迁移。
