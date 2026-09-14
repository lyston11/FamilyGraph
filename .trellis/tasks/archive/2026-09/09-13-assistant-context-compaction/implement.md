# Implement：Assistant 压缩一致性

## 前提

已获执行授权，主检出已 start 本任务。分支 feat/09-13-assistant-context-compaction，worktree /private/tmp/familygraph-memory-rag/09-13-assistant-context-compaction，基于 A 提交 d1f43a5。读取父审计 MR-11/MR-12 与 Pi 锁定版本。A 可独立完成；默认顺序 A→C→B，避免与 B 改 sidecar 同文件。

## 执行

- [x] 将已有离线失败复现写成实际 SDK 回归，先确认 manager 未包含恢复历史的失败。
- [x] 把历史转换移至 createAgentSession 前，用公开 appendMessage 填同一个 manager；删除 state 单独覆盖。
- [x] 覆盖最新 user、消息 ID 去重、重复文本、空/异常历史及恢复无模型副作用。
- [x] 分别补手动与自动压缩测试：旧事实进入摘要、继续调用可见。
- [x] 回归 Run 重建、工具/RAG 不重放、取消、lease loss 与 Provider snapshot。
- [x] 记录边界：不新增持久摘要，不声称全请求 token 预算完成，交接 B。

## 验证

在 agent 执行 `npm run lint`、`npm run type-check`、`npm test`、`npm run build`；假模型测试使用实际 SDK，禁止真实 egress。
若实际改动涉及后端 context，补 internal_agent_api/schema_contract 回归及真实合同联调；纯 session 修改不无故扩展后端实现。

## 完成

C-AC1～6 的结果与 SDK 版本、自动/手动触发证据落 notes。不能仅凭手动 compact 或现有单用户 fixture 通过交付。没有迁移；不改数据库聊天内容。
