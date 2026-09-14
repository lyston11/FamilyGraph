# Assistant 历史恢复与 Pi 压缩合同

本文件记录 C 任务的实际接线，不将历史 spec 重新设为执行门禁。任务入口：[Assistant 历史恢复](../../tasks/archive/2026-09/09-13-assistant-context-compaction/prd.md)；实现位于 `agent/src/session.ts`，SDK 锁定 Pi 0.84.3。

## 1. Scope / Trigger

修改 sidecar 会话创建、历史投影、当前 prompt、SDK 压缩或 Run 重建时，同时核对模型状态和 SessionManager 的数据来源。

## 2. Signatures

`buildRunSession(config, client, projection, runToken, deps)` 创建一轮独立 Pi 会话。历史预填使用公开的 `SessionManager.inMemory(agentDir)`、`appendMessage(message)` 和 `createAgentSession({sessionManager, model, ...})`。

## 3. Contracts

先将后端按持久 ID 排序的允许历史转换并 append 到同一个 manager，再创建 SDK session。按 ID 去重，相同文本的不同消息仍各自保留。排除当前 user ID，由 worker 的 `session.prompt` 发送一次；预填不能调用 prompt、工具或发出新的公开 user_added 事件。

恢复仅接受 user/assistant 的 content_json.text。旧工具结果、独立 RAG blocks、thinking、Provider 私有状态和引用元数据不转换为历史消息。Assistant 已持久正文按原会话保留合同恢复；其中已有的来源事实不由本修复擦除。

恢复的 Assistant 文字没有历史 Provider usage，沿用零 usage 表示缺失；不得把它计为真实计费。显式传入本轮绑定的 model，不能由历史覆盖 Provider/model 决策。每次 Run/attempt 新建内存 manager，不共享其他账户会话，也不持久化 Pi 压缩摘要。

保留 SDK 默认自动压缩。Pi 从 manager 读取压缩源，再用压缩条目重建模型状态；仅给 agent.state.messages 赋值会使压缩后历史丢失。RAG 本轮只由 worker 注入一次，不作为旧历史预填。

## 4. Validation & Error Matrix

| 场景 | 结果 |
|---|---|
| 空历史或只有当前 user | manager 的消息为空（可有模型配置元数据），当前 user 在 prompt 时加入 |
| 重复 ID / 同文本不同 ID | 同 ID 只恢复一次；不同 ID 均保留 |
| 手动 compact | 摘要请求包含早期事实，继续 prompt 能使用摘要 |
| 实际自动阈值触发 | SDK 发出 compaction_start/end，reason=threshold；摘要与后续请求保持旧事实 |
| Provider overflow 且摘要失败 | 可解释失败，无静默截短或伪造成功 checkpoint，原持久 transcript 不改 |
| Provider overflow 后压缩与重试成功 | 完整 stop 回答解除早先 Provider 错误，worker 正常结算；取消、失租与策略拒绝仍独立优先 |
| Run 重试/新会话 | 从允许的持久正文重新构造，不复用上一轮摘要或临时 RAG |

## 5. Good / Base / Bad Cases

Good：原 user 事实和 Assistant 确认先进入 manager，长会话被自动压缩后，下一次模型请求包含该事实的摘要。

Base：没有旧消息的首次会话、Provider 绑定、取消和 lease loss 继续遵守既有 worker 合同。

Bad：createAgentSession 后单独覆盖 agent.state.messages，或通过关闭自动压缩/recent-N 截断让测试通过。

## 6. Tests Required

`agent/test/session-history.test.ts` 使用实际 Pi 与假 provider stream，拦截网络：预填一致性/无副作用、ID 边界、允许正文过滤、手动压缩、真实 prompt 前后自动阈值压缩、overflow 失败与 Run 重建。`worker.integration.test.ts` 保留当前 user/RAG 一次性、取消、lease loss 和 Provider 回归。

自动路径必须经过实际 session.prompt；不能只调用 compact、私有压缩方法或 mock manager。测试结果证明接线和协议，不能替代真实模型摘要质量评估。

worker 同时覆盖 overflow 后恢复成功与重试再次失败。只收到完整 Assistant stop 才解除旧 Provider 错误；工具结果、partial 或压缩事件不代表回答成功。超大当前 user 的失败回归必须验证原输入仍完整且仅出现一次。

## 7. Wrong vs Correct

错误：将“state 里有历史”当成“压缩源里有历史”，或将 C 完成宣称为全请求预算/跨 Run 持久摘要完成。

正确：在创建前向同一 manager 预填，以实际 SDK 自动与手动压缩证明旧事实进入摘要。全请求预算和带来源权限的持久摘要由 E 的研究方案另行约定。
