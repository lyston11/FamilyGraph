# 助手链路优化：空事件过滤与会话压缩

> 父任务：09-15-agent-audit-remediation。优先级 P2。

## 背景

run #2 时间线显示：首个 turn 模型输出空文本 assistant 消息 + 一次工具调用，空文本 `message.assistant_added` 事件仍走完整 append→SSE→前端渲染；会话历史每轮全量重放（internal_agent.py 注释明确这是防模型自相矛盾的取舍），长会话线性变慢。实测 33s 模型推理是主要延迟（上游中转），本任务只清理我们可控的两项。

## Requirements

- R1 空事件过滤：sidecar `events.ts` 对 `message_end` 且 assistant 文本为空、且该 turn 已有 tool 调用事件时，不产生 `message.assistant_added` 公开事件（内部仍需保留 turn 计数语义；确认 Pi 会话重建不依赖该空消息）。若空消息在 sessionManager 重放中承载占位作用，改为仅在公开事件流过滤。
- R2 会话压缩：对长会话（超过阈值，如 30 条消息或估算 token 超过预算）在 context 投影层引入摘要——方案先行设计：可以用「早期消息确定性摘要（首条+近 N 条全量）」或 Pi compaction，必须保持引用与事实可追溯，摘要来源要标注 trust，不得混入 RAG 引用链。
- R3：SSE/前端确认过滤后无 UI 空消息闪现回归。
- R4：测试：空事件过滤单测；压缩投影的单测（消息数、顺序、摘要标记）。

## Acceptance Criteria

1. 新 run 时间线无空文本 `message.assistant_added`。
2. 长会话（>阈值）的 context 投影消息数受控，模型行为不自相矛盾（人工验证一轮多轮对话）。
3. 现有 agent 相关测试全部通过。

## 回滚

R1 单点 revert；R2 建议 flag 控制。
