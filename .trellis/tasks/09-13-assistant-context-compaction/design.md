# Design：用同一 SessionManager 恢复历史

## 1. 最小正确路径

先完成历史过滤与转换，再创建 SDK session：

```text
durable messages（按 ID）
  → 只取 user/assistant text，按当前消息 ID 排除最新 user
  → manager = SessionManager.inMemory(agentDir)
  → manager.appendMessage(history message)，每 ID 一次
  → createAgentSession({..., sessionManager: manager, model: currentModel})
  → worker.prompt(current user + current RAG)
```

删除 create 后直接覆盖 agent.state.messages 的代码。使用公开 API，不调用 prompt/sendUserMessage 来重放历史，不写文件会话。

依赖证据（Pi 0.84.3，dist/core）：
- session-manager.d.ts:217/333 公开 appendMessage/inMemory。
- sdk.js:81 调 buildSessionContext，:239 恢复 state，:253 传同 manager 给 AgentSession。
- sdk.js:84 优先 options.model，所以恢复历史不会覆盖本轮服务端绑定的 Provider/model。
- agent-session.js:1410/1674 分别为手动/自动读取 manager；:1465/1752 用 manager 重建 state。

## 2. 历史映射与副作用

保留后端稳定 ID 顺序。去重键是消息 ID，不是文本。最新 user 的 ID 从当前合同确定；现有 run.message_id 与同 session 单 active run 约束已保证正常消息路径，最小修复无需为此扩 wire。

user/assistant text 转换沿用当前模型字段的合法 Pi Message 形状，避免把虚构 usage 当实际账单。当前 RAG 在 prompt 中注入一次，不能先作为历史预填又在 worker 拼一次。历史中的 UI 引用元数据只用于展示，不变成绕过当前授权的正文来源。

已持久的 Assistant 正文仍按既有会话合同恢复，其中可能包含以前回答的来源事实；这与单独重放旧 RAG 数据块不同。C 不负责撤回历史回答或物理擦除，相关保留/摘要权限政策由 E 单独设计，不能宣称本修复消除了历史派生文本。

SDK 预填阶段不得创建公开 message.user_added、产生工具调用或触发模型。Run 重试从持久 transcript 构造新的内存 manager；不复用另一个账户/空间的 manager。

## 3. 压缩与预算边界

保留 Pi 默认自动压缩，不以关闭它规避错误。手动/自动共享正确 manager 后，摘要必须包含早期历史，而不是只有本轮。

本子任务不新增全请求 token 预算，也不保证任意长度历史永远成功。SDK 压缩前后和 Provider 过限应产生可解释结果；测试不得通过静默 recent-N 截断制造成功。完整 history/current user/RAG/system/tools/output reserve 的预算由 E 独立设计。

不持久化跨 Run 摘要，因为现有 Pi 摘要可能包含当轮 RAG/工具材料；直接存入以后 Run 会突破来源权限和“原始材料不重放”的边界。

## 4. 回归构造

基于实际 buildRunSession 和已安装 SDK，以 streamOverride 假流、假 client、阻断 global fetch：
- 构造旧 user 提出一个唯一事实、旧 assistant 确认、当前 user 提问。
- 断言初始化后的 manager/state 都含旧事实，且最新 user 未重复。
- 手动压缩假流捕获摘要请求并返回包含事实的摘要，再继续一轮验证。
- 自动路径用受控上下文窗口与 usage/长文本触发，明确验证真实自动事件；不能把手动 compact 的结果冒充自动。
- 另测空历史、单条 user、相同文本不同 ID、失败重试、取消与本轮 RAG 一次性。
- 测试只用合成事实，不输出真实对话或模型凭据。

## 5. 文件与兼容

主要修改 agent/src/session.ts，新增专门 history/compaction 测试并复用 worker.integration.test.ts。不改 node_modules，不新增 migration；如 SDK 公开行为与锁定版本不同，先更新证据而不改依赖来绕开。

B 后续修改 client/worker/events/schema 引用时须保留本套回归。现有 provider projection、policy hooks、服务端唯一 egress 和事件白名单不变。

## 6. 回滚

补丁回滚只涉及恢复接线；不迁移/删除持久历史。若无法可靠自动压缩，保留明确失败，不静默关闭压缩后把完整超长请求发送。任何跨 Run 摘要方案须另审阅。
