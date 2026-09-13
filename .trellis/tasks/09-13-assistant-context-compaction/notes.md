# Notes：Assistant 历史恢复与压缩一致性

SDK 版本：`@earendil-works/pi-ai` / `@earendil-works/pi-coding-agent` 均为 0.84.3（`npm ls` 确认，未改 node_modules）。基线：A 提交 `d1f43a5`。

## 结果摘要

`agent/src/session.ts` 的 `buildRunSession` 改为：在 `createAgentSession` 之前，把后端按持久 ID 排序的允许历史（仅 user/assistant `content_json.text`）通过公开的 `SessionManager.inMemory(agentDir).appendMessage()` 预填进同一个 manager；按消息 ID 去重（不按文本）；排除最新 user ID（由 worker.prompt 发送一次）；Assistant 文字带零 usage；显式传入本轮绑定的 model。删除了 create 之后直接覆盖 `agent.state.messages` 的路径。

## 验收映射（详见 research/implementation.md）

- C-AC1：`session-history.test.ts:267` manager/state/首次模型请求一致；`:295` 250 条历史按 ID 完整保留。
- C-AC2：`:190` 空历史/单 user/连续 user/同文本异 ID/重复 ID 参数组；预填零模型调用、零 user_added 事件；worker `:709` 仅一次后端 user_added。
- C-AC3：`:390` 真实 `session.compact()` 摘要读到早期事实，后续 prompt 使用摘要。
- C-AC4：`:411` 响应后 threshold、`:453` 提交前 threshold，均为真实 SDK 自动触发（compaction_start/end, reason=threshold），无手动 compact 替代。
- C-AC5：`:309` 仅恢复正文，旧工具/RAG/thinking/provider 块不入恢复；`:510` attempt 重建；`:536` manager 与摘要不跨 Run/账户；当前 RAG 只注入一次。
- C-AC6：`:357` Provider/model 绑定保持；`:471` 过限产生明确 `compaction_end(reason=overflow)` 且不静默删历史；worker 取消/租约撤销/Provider 拒绝回归通过。

红测证据：业务修改前三条核心回归失败（state 有历史、manager 为空；手动压缩报 "Nothing to compact"；自动压缩无 compaction 事件），修复后全部通过。

## 检查记录（2026-09-13 23:37 Asia/Shanghai，本 worktree agent/）

- `npm run lint` / `npm run type-check` / `npm run build`：通过
- `npm test`：13 个文件 108 条全部通过（新增 21 条：SDK 专项 19 + worker 集成 2）
- 未运行后端/前端全包与 listener smoke：本次未改这两端代码与协议。

## 边界与交接

不新增跨 Run 持久摘要、不保存完整 Pi 会话、不做全请求 token 预算（归 E）。历史 assistant 正文中既往来源事实不因此擦除（归 E 政策）。RAG 查询/引用归 B；B 修改时须保留本套回归。无迁移，不改数据库聊天内容。
