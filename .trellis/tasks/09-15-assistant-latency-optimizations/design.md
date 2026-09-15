# 助手链路优化：核查结论与技术设计

## 0. 核查结论

### R2「长会话无摘要压缩」——前提被证伪

| PRD 原假设 | 实测结论 |
|---|---|
| 长会话每轮重放全量历史、无压缩机制，需引入摘要 | **证伪**。压缩已存在且已接线（见下），且已归档 spec 明确**要求保留**，并把「关闭自动压缩 / recent-N 截断」列为错误做法。 |
| 33s 延迟来自无压缩导致的长 prompt | **证伪**。该 run 仅 2 turn、6 条消息（生产库实测），33s 花在模型生成。 |

证据：

1. `agent/src/session.ts:365` 用无参 `SettingsManager.inMemory()`；Pi 默认 `DEFAULT_COMPACTION_SETTINGS = { enabled: true, reserveTokens: 16384, keepRecentTokens: 20000 }`（`pi-coding-agent/dist/core/compaction/compaction.js:74-78`）。
2. 压缩源是**预填过的 SessionManager**，不是 `agent.state.messages`——这正是已归档 spec `assistant-history-restoration.md` §3 的核心合同（"Pi 从 manager 读取压缩源，再用压缩条目重建模型状态；仅给 agent.state.messages 赋值会使压缩后历史丢失"）。
3. 该 spec §6 已要求并有测试覆盖：`test/session-history.test.ts` 的「真实 prompt 前后自动阈值压缩」「overflow 失败与 Run 重建」；`worker.integration.test.ts:953` 断言压缩请求的 systemPrompt 为 "context summarization assistant"，且 `_checkCompaction` 的 overflow 分支带重试（`agent-session.js:1584-1599`）。
4. 该 spec §5 把「通过关闭自动压缩 / recent-N 截断让测试通过」列为 **Bad**。
5. `backend/app/api/internal_agent.py` 的完整历史投影注释明确：截断到 recent-N 会静默丢弃早期 user/assistant turn，**让模型自相矛盾**。投影层摘要同理。

**决策**：不新增压缩。再叠一层会与既有 SDK 压缩双源冲突；截断中段历史违反既有注释与 spec。R2 放弃，理由记入 PRD。

### R1「空文本 assistant 事件」——真实缺陷，影响比原描述更严重

生产 run #2 时间线：`+5.2s` 首个 turn 的 `message.assistant_added` 文本为空（该 turn 只发工具调用），`+38.4s` 才出真正回答。核查发现三处实际后果：

| 后果 | 证据 |
|---|---|
| **进行中指示提前消失 33 秒** | `MessageList.vue:86-88`：`showPendingIndicator = runActive && !messages.some(m => m.role === 'assistant')`。空消息一落地，指示立即消失，而 run 到 +38.4s 才结束——用户在 33 秒内看不到任何进行中反馈。 |
| **空消息被持久化** | `internal_agent.py` 对每个 `message.assistant_added` 建 `AgentMessage` 行。生产库 session 4 实测：`m.id=5, role=assistant, len=0`。 |
| **空历史被重放给模型** | 同一行在后续 run 的完整历史投影中作为空 assistant 消息下发（`internal_agent.py` 的 `recent` 全量投影）。 |

### 已排除的非问题

- 引用绑定不受影响：工具 turn 的 `stopReason === "toolUse"`，本就不满足 `context_reference` 的绑定条件（仅 `stopReason === "stop"`，见 `events.ts` 与 `events.test.ts` 的 "does not turn a legacy no-build answer or tool transcript into a reference"）。
- web 引用收集不受影响：`fetch_approved_page` 的 citation 在 `tool_execution_end` 收集。过滤空消息后，单工具 turn 的引用照旧附着到最终回答；而**多**工具 turn（fetch 后还有一个无正文工具 turn）原本会被中间那条空事件截走，过滤后改为直接附着到真正的最终回答——是净改善。仅当 run 以工具 turn 收尾（只可能是 policy block 导致的 failed，或取消）时缓冲引用无处附着，此时本就没有回答可展示引用。

## 1. 改动范围

| 文件 | 改动 |
|---|---|
| `agent/src/events.ts` | `message_end` 分支：assistant 文本为空**且** content 含 `toolCall` 块时返回 `[]` |
| `agent/test/events.test.ts` | 新增：工具 turn 空文本不产事件（正例）、有正文的工具 turn 照常产事件、无工具调用的空回答**仍产事件**（边界：留给后续项） |
| `frontend/src/components/agent/MessageList.vue` | `showPendingIndicator` 忽略空文本 assistant 消息（一行防御，见 §2） |
| `frontend/src/components/agent/__tests__/AgentPrimitives.spec.ts` | 新增：仅工具事件到达时指示保持可见；空文本 assistant 消息不熄灭指示 |

后端零改动（无事件即无消息行）。

## 2. 设计

### 过滤判据

```ts
// events.ts，message_end 分支
const text = extractText(event.message.content);
const hasToolCall = Array.isArray(event.message.content)
  && event.message.content.some(
    (b) => typeof b === 'object' && b !== null && (b as { type?: unknown }).type === 'toolCall',
  );
if (text.length === 0 && hasToolCall) return [];
```

- 用「content 含 `toolCall` 块」而非 `stopReason === "toolUse"`：直接表达「这条消息只承载工具调用、没有正文」，不依赖各 provider 的 stopReason 命名（`openai-responses`/`google-generative-ai`/`google-vertex` 都设 `toolUse`，但判据应表达意图而非枚举实现）。
- 只过滤「空文本 **且** 有工具调用」。有正文的工具 turn（模型边说边调工具）照常上报——那是真实回答内容。
- 「空文本且无工具调用」**不过滤**：那是 provider 返回空答案的独立缺陷（空气泡），语义与处置不同，列为后续项。

### 为什么前端也要一行防御

侧车过滤是源头修复，但前端消费的是服务端事件流——这是一条边界。空事件一旦从任何路径重现（回滚、旧版 sidecar 在滚动重启窗口内、未来新增事件路径），用户立刻回到「33 秒无反馈」的严重症状。一行的判据收紧代价极低：

```ts
const showPendingIndicator = computed(
  () => runActive.value && !props.messages.some((m) => m.role === 'assistant' && m.text.length > 0),
)
```

- 不影响既有语义：真正回答到达（文本非空）时指示照常让位；run 终态时 `runActive` 为 false 照常熄灭。
- 不掩盖后续项：空最终回答时 run 已终态，指示本就应熄灭，与本次收紧无关。

### 不改的东西

- 不新增/修改压缩（理由见 §0）。
- 不改 `turn.started/turn.completed` 计数语义——工具 turn 仍是一个 turn。
- 不改工具事件（`tool.execution.started/completed`）——工具调用的可观测性由它们如实承载，不因过滤而丢失。
- 不清理已落库的历史空消息：无害，回改属扩大范围。

## 3. 兼容性

- 事件序列：过滤后 seq 连续由 `RunEventBuffer.nextSeq` 自然维持（它按产出的条目递增，跳过即不占号）。后端 `expected_next += 1` 亦按提交条目推进，不要求 seq 连续（`agent_events.py`）。
- 既有测试：`events.test.ts` 的 "does not turn a legacy no-build answer..." 用例用的是 `stopReason: "stop"` + 无 toolCall 块，**不触发**新过滤（无 toolCall），照常通过。
- `worker.integration.test.ts` 的 toolUse fixture（3 处）需核对文本是否为空；非空则不受影响。

## 4. 测试设计

**`agent/test/events.test.ts`**（沿用既有 `mapSessionEvent` 直调风格）：

1. 空文本 + toolCall 块 → 返回 `[]`。
2. 有正文 + toolCall 块 → 仍产 `message.assistant_added`，正文完整。
3. 空文本 + 无 toolCall（空最终回答）→ **仍产事件**（锁定边界，防误扩大过滤）。
4. 既有 "legacy no-build answer" 用例保持通过（回归）。

**`frontend/src/components/agent/__tests__/AgentPrimitives.spec.ts`**（沿用既有 mount 风格）：

1. run active + 仅空文本 assistant 消息 → `thinking-indicator` 仍存在。
2. run active + 非空 assistant 消息 → 指示消失（既有语义不回归）。
3. run 终态 → 指示消失（既有用例已覆盖，不改）。

## 5. 后续项（本任务不做，写入触发条件）

- **中文 token 估算校准**：Pi 的 `estimateTokens` 用 `chars/4`（实测：中文 520 字 → 130 token；英文 1880 字 → 470 token，两者都是 4.00 chars/token）。中文真实 tokenizer 约 1 字 1 token，故中文会话下阈值压缩触发约晚 4 倍。当前无生产证据（最长会话 6 条消息），且 overflow 自动恢复已兜底（`_checkCompaction` overflow 分支 + 重试，已有测试覆盖）。触发条件：出现真实 overflow 恢复或单会话消息数显著增长时，评估自定义 `compaction.reserveTokens`。
- **空最终回答**：正文空且无工具调用的 turn 会产出空气泡。独立缺陷，需单独设计与验证（涉及「该不该结算为 succeeded」的产品判断）。
