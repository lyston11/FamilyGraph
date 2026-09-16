# 助手空最终回答：核查结论与技术设计

## 0. 核查结论（含一处对我先前判断的修正）

### 修正：生产里那条空 assistant 消息**不是**本缺陷

我最初把生产库的 `agent_messages.id=5`（`text=""`）当作本缺陷的实例。逐事件核对后**推翻**：

```
run 2 事件:  3|message.assistant_added|{"text": ""}     ← 空正文
             4|tool.execution.started|{"tool_call_id": "call_6WRP5bem…"}
             5|tool.execution.completed|…
```

`seq=4/5` 有工具执行，说明它是**工具 turn**（`stopReason === "toolUse"`），已被
09-15 `assistant-latency-optimizations` 的过滤修掉。`run 1` 的 `seq=3` 同理（且有正文）。

**结论：生产目前没有本缺陷的实例**，不得声称「线上已发生」。

### 但代码路径可达，三层证据齐全

| 层 | 证据 |
|---|---|
| sidecar 事件映射 | `events.ts:191` 的判据是「正文空 **且** 含 toolCall 块」→ 正文空且无工具调用时**照常产出** `{text: ""}` 事件 |
| sidecar 结算 | `worker.ts:295` 只检查 provider 错误，**没有检查是否真的产出了答案**，随后无条件 `settleRun(..., "succeeded")` |
| 后端 + 前端 | 后端为 `message.assistant_added` 建 `AgentMessage` 行（`agent_events.py:255`）；`MessageList.vue` 的 `{{ item.text }}` 无空值保护 → 可见空气泡 |

`agent/test/events.test.ts:89` 有一条测试 **`keeps an empty answer without tool calls (boundary of the filter)`**
正在**固化**这个行为（断言产出 `{text: ""}`）。它是「按设计如此」的显式边界，不是疏漏。

### 为什么比「空气泡」严重：虚假成功

run 结算 `succeeded` → `finishRun(partition, 'succeeded')` 不设 `partition.error`
→ `ErrorNotice` 不渲染 → **用户既没有答案，也没有任何解释**。同时空 assistant 行被持久化，
并作为**空历史**重放进下一次 run（`internal_agent.py:522` 只要 `content_json["text"]` 是字符串就纳入，空串符合）。

## 1. 改动范围

**sidecar + 前端**（后端零改动）：

| 文件 | 改动 |
|---|---|
| `agent/src/worker.ts` | 追踪最后一条 assistant 消息正文；空则 `failed` + `PROVIDER_EMPTY_ANSWER` |
| `agent/src/events.ts` | 导出 `extractText`（复用同一判据，不另写文本提取） |
| `agent/src/events.ts` | 事件过滤判据从「正文空 **且** 有 toolCall」收紧为「正文空」 |
| `agent/test/events.test.ts` | 更新 `boundary of the filter` 用例（原断言固化旧行为） |
| `agent/test/worker.integration.test.ts` | 新增空回答结算用例 |
| `frontend/src/components/agent/MessageList.vue` | 空正文 assistant 消息不渲染气泡本体 |
| `frontend/src/api/agent.ts` | `AGENT_ERROR_COPY` 增 `PROVIDER_EMPTY_ANSWER` |
| `frontend/src/api/__tests__/agentErrors.spec.ts` | 新增文案映射用例 |

## 2. 设计

### 2.1 R1：结算判定（worker）

复用 `mapSessionEvent` 用的**同一个** `extractText`，避免两处判据漂移：

```ts
// 与 lastAssistantError 并列。独立的 if，**不修改**现有 if/else-if 链
// （否则会把 lastAssistantError 的清空条件从 "stop" 扩到 "aborted" 等，属无关行为变更）。
const lastAssistantText: { current: string | null } = { current: null };

// 与现有 lastAssistantError 分支并列，新增一个独立分支：
if (
  raw.type === "message_end" &&
  raw.message?.role === "assistant" &&
  // 只有「生成完成」才是最终回答：stop=完整，length=截断（仍有正文）。
  // 排除 toolUse（不是答案）、error（第 3 步优先接管）、aborted（服务端裁决）。
  (raw.message.stopReason === "stop" || raw.message.stopReason === "length")
) {
  lastAssistantText.current = extractText(raw.message.content);
}
```

> **为什么必须包含 `length`（设计要点，勿"修正"）**：只记录 `stop` 会与 §3.2 冲突——
> `stopReason === "length"`（截断）但有正文的最终回答将永不被记录，`lastAssistantText` 保持 `null`，
> 被误判为 `failed`，造成**现状回归**。
> 「后者覆盖前者」保证循环中**最后一条**就是最终回答：工具 turn 的正文会被随后空的 `stop` 消息覆盖
> （正确判失败），而 `length` 有正文的最终回答能正常计为成功（不回归）。

结算顺序**在既有分支之后、`succeeded` 之前**插入：

```
1. leaseLost / cancelRequested        → return（不变，服务端裁决）
2. policyGuard 阻断                    → failed POLICY_*（不变）
3. lastAssistantError                  → failed PROVIDER_STREAM_ERROR（不变，优先级更高）
4. lastAssistantText 为空              → failed PROVIDER_EMPTY_ANSWER（新增）
5. 其余                                → succeeded（不变）
```

第 3 步必须优先：provider 错误的消息正文同样为空，但用户需要看到的是「模型服务不可用」而非「模型没有返回内容」。

`lastAssistantText === null`（完全没有最终回答）也归入失败——R1 要求「只有在**产出**非空最终回答时才成功」。

### 2.2 R2：事件过滤收紧

```diff
-  if (text.length === 0 && hasToolCall) return [];
+  if (text.length === 0) return [];
```

`hasToolCall` 随之删除（不再需要区分工具 turn）。三种情形：

| 情形 | 过滤前 | 过滤后 |
|---|---|---|
| 工具 turn，无正文 | 过滤 | 过滤（不变） |
| 工具 turn，有正文 | 上报 | 上报（不变） |
| **空最终回答** | **上报 `{text:""}`** | **过滤（修复）** |

**R1 与 R2 必须同批发布**：只做 R2 会把「可见空气泡」变成「静默成功」（连空气泡都没有，
用户完全不知道发生了什么），比现状更糟。这一点是设计的硬约束。

### 2.3 R3：前端不渲染空气泡

只隐藏**气泡本体**，不动该消息上的结构化内容（`cardIds` / `citations` / `webCitations`）：

```html
<div v-if="item.role === 'user' || item.text.length > 0" class="bubble" …>
<span v-if="item.role === 'user' || item.text.length > 0" class="sr-only">…说</span>
```

sr-only 标签与气泡同条件：否则屏幕阅读器会播报「助手说」却没有任何内容。
用户消息始终渲染（`sendMessage` 已 trim 并拒绝空正文，不存在空用户消息的合法形态）。

### 2.4 R4：错误文案

`frontend/src/api/agent.ts` 的 `AGENT_ERROR_COPY` 在 `PROVIDER_STREAM_ERROR` 旁新增：

```ts
PROVIDER_EMPTY_ANSWER: '模型没有返回内容，请重试或换个问法',
```

**不注册进 `backend/app/errors.py`**：实测约定是「**后端会发出的**码必须注册，纯 sidecar 运行期码只在
前端文案表 + spec 登记」。`errors.py` 中有 `POLICY_TOOL_BLOCKED`（后端也在输入侧发出），而
`PROVIDER_STREAM_ERROR`/`SIDECAR_ERROR`/`POLICY_SECRET_LEAK`/`PROVIDER_DENIED_*` 均**不在**其中，
只存在于 `AGENT_ERROR_COPY`。`PROVIDER_EMPTY_ANSWER` 由 sidecar 独占发出，属后者。
后端零改动。`error_code` 列宽 `String(64)`，`PROVIDER_EMPTY_ANSWER`（21 字符）无需迁移。

## 3. 影响面与兼容性

### 3.1 记忆提取的耦合（**已接受的后果，需显式记录**）

`agent_queue._settle` 的提取钩子条件是 `effective == "succeeded"`。空回答 run 改为 `failed` 后，
**该次 run 不再触发记忆候选提取**。

- 提取只读**用户消息**（`run.message_id`，要求 `role == "user"`），与助手回答无关。
- 接受理由：空回答意味着这轮交互没有完成，用户重发时会重新产生候选；且空回答在生产中为 0 例，实际影响 ≈ 0。
- 不为此改后端：PRD 要求后端零改动，且「failed 不跑成功路径副作用」本身是自洽语义。

### 3.2 `stopReason === "length"`（截断）与「仅空白」边界

- 截断**有正文** → 仍 `succeeded`（**与现状一致**，不回归）——由 §2.1 把 `length` 纳入
  `lastAssistantText` 的判据保证；若按 `stopReason === "stop"` 过滤会在此处回归。
- 截断**无正文** → `failed` + `PROVIDER_EMPTY_ANSWER`（新增保护；生产无实例、无测试覆盖）。
- 未把 `length` 单独建模为错误码：它属「部分答案」而非「无答案」，需单独设计（§5）。
- **仅空白正文**（如 `"\n"`）：本任务按 `length > 0` 视为非空，**不**做 trim。理由：
  `assistant-history-restoration.md`/09-15 已把前端的判据记录为 `m.text.length > 0`，
  worker 与前端必须用同一个谓词，否则会出现「指示器熄灭但气泡也隐藏」的静默空态。
  仅空白回答是未经证实的推测场景（生产 0 例），如需处理应在 sidecar 单点归一（trim 后为空则不产事件），
  作为后续项记录在 §5。

### 3.3 其他

- 取消/租约丢失路径：新检查位于 `leaseLost/cancelRequested` 早返回**之后**，不会产生新的 `failed` 结算。
- 后端契约不变：`SettleRequest.status` 已允许 `failed`，`error_code` 为自由字符串（≤64）。
- 遗留数据：`agent_messages.id=5` 保留在库中，由 R3 的渲染保护隐藏；不做数据清理（需独立数据修复流程与审计）。
- **web citations 不再挂到空回答**：`RunEventBuffer.onSessionEvent` 把本 turn 收集的
  `fetch_approved_page` 引用挂到**下一条** assistant 消息；R2 后空回答不产消息，引用留在 buffer。
  该 run 本就 `failed`、没有可展示的答案，不存在“引用丢失”的回归；若未来需要保留，
  应在失败态单独呈现（§5）。
- 既有测试 `AgentPrimitives.spec.ts:227`（工具 turn 空消息不熄灭指示）只断言指示器存在、
  **不**断言空气泡，R3 不使其回归。

## 4. 测试设计

### agent

1. **新增**：模型只返回空最终回答（`stopReason: "stop"`、`content: [{type:"text",text:""}]`、无工具调用）
   → 结算 `failed` + `error_code: "PROVIDER_EMPTY_ANSWER"`，且**不产** `message.assistant_added` 事件。
2. **更新** `events.test.ts` 的 `boundary of the filter`：断言由 `[{text:""}]` 改为 `[]`，用例改名
   （原断言固化的正是被修复的行为，属**刻意的行为变更**，不是弱化测试）。
3. **不回归**（沿用既有断言逐字不动）：`PROVIDER_STREAM_ERROR`（provider 错误）、
   工具 turn 过滤、压缩后重试成功、policy 阻断、取消/租约丢失。
4. 用 `worker.integration.test.ts` 的真实链路（真 worker + Pi session + 工具执行 + mock FastAPI），
   不只做单元断言。

### frontend

5. 空正文 assistant 消息不渲染 `[data-test="message-item"]` 的气泡文本（空气泡消失）。
6. 空正文但带 `cardIds` 的 assistant 消息**仍渲染卡片**（R3 不得丢弃结构化内容）。
7. 用户消息（含空正文）仍渲染气泡。
8. `friendlyAgentError('PROVIDER_EMPTY_ANSWER')` 返回中文解释而非通用兜底。

## 5. 后续项（本任务不做）

- **`length` 截断建模**：截断属「部分答案」，可能需要「继续生成」而非报错。
- **仅空白正文归一**：若实测出现仅空白回答，在 sidecar 单点 trim 后判空（避免前端/worker 谓词分叉）。
- **一键重发**：失败后自动/一键重发上一条用户消息（需要草稿与重发语义，R5 明确不做）。
- **后端不变式**：`succeeded` 必须至少有一条 assistant 事件（需回查事件，属重复真源）。
- **遗留数据清理**：生产库空 assistant 行的数据修复流程。
