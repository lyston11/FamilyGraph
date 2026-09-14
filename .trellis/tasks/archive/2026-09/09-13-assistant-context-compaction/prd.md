# PRD：Assistant 历史恢复与压缩一致性

## Goal

让 Assistant 恢复的旧对话进入 Pi 真正使用的压缩上下文，避免长会话压缩后本轮遗忘旧事实。

父任务：[治理总任务](../09-13-agent-memory-rag-remediation/prd.md)。所有者 C；P1/MR-11，兼顾 MR-12 的边界。用户已批准执行；A 已验证并提交，本任务从 A 提交串行实施。

## Background

`agent/src/session.ts:377` 每 Run 新建内存 manager，:420 仅把历史赋给 agent state。Pi 压缩从 manager 取源。已离线复现 state 里有两条历史、manager 里没有；成功手动压缩后旧历史从本轮上下文消失。实际 SDK 为 0.84.3；默认自动压缩存在，生产自动触发频率未测。

## Requirements

- C-R1：历史按持久 ID 顺序进入同一个 Pi session manager 和模型上下文，不触发新的模型 turn。
- C-R2：本轮 user 由 worker 发送一次；相同文本不同 ID 不合并；空历史、单条 user、连续 user 均可解释处理。
- C-R3：自动与手动压缩均可看到较早事实，压缩后的回答仍可使用其摘要；保留原始数据库历史，不要求所有旧文本继续原样占用 context。
- C-R4：恢复只包含既有允许的 user/assistant text；不跨 Run 重放旧工具结果、RAG blocks、thinking 或 provider 私有状态。
- C-R5：Provider/model、取消、租约、会话并发与重试合同保持；超限不能静默删除历史后声称恢复成功。
- C-R6：准确说明本修复范围；不把局部 Pi 修复称为全请求预算或跨 Run 持久摘要。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| C-AC1 | SDK 初始化后 manager 分支和模型上下文都含全部允许历史；当前 user 仍只在 prompt 时加入一次 |
| C-AC2 | 重复文本不同 ID 保留，重复同 ID 不二次恢复；历史恢复没有模型调用和 user_added 事件副作用 |
| C-AC3 | 离线手动压缩时摘要请求看到旧事实，压缩后继续请求看到携带该事实的摘要 |
| C-AC4 | 实际 SDK 的自动触发回归满足同样条件；不只直接调用 compact 测手动路径 |
| C-AC5 | 进程/Run 重建保留数据库历史，旧工具/RAG/内部思考不入恢复消息；本轮 context 只注入一次 |
| C-AC6 | 同意/取消/租约丢失和 Provider 绑定回归通过；长历史过限得到可解释结果，非静默截断 |

## Out of scope and handoff

不改数据库持久化，不存完整 Pi session，不做跨会话记忆，不引入跨 Run 摘要或全请求预算；这些归 E。RAG 查询/引用归 B，C 完成后默认串行交给 B，避免同时修改 session/worker。实现前需审阅本 PRD/design/implement。
