# B 实施分工与串行点

A/C 提交后，从 C 的已验证提交创建 B 分支。以下分工用于同一 B worktree 内不相交文件的实施；迁移只有一个写入者。各执行者不得提交、回退或覆盖其他人的文件。

| 责任 | 独占写入范围 | 必须交接 |
|---|---|---|
| 检索与上下文 | `services/memory_rag.py`、新 query/chunk helper、`context_builder.py`、`models/rag.py`、相应检索/上下文测试；必要的来源精确解析 helper | query plan/追问接口、实际 chunk 定位与 hash、RAG 预算、ContextBuilder 的 attempt/build 重用行为；模型变更交迁移所有者 |
| 后端引用协议 | `agent_events.py`、`agent_tokens.py`、新 citation helper、`api/internal_agent.py`、`api/agent.py`、`schemas/agent.py`、`models/agent.py`、`models/context.py`、**本任务全部迁移**、相应协议测试 | 新 token attempt、context_reference、fallback API、当前权限投影、原请求 fingerprint；先给另两责任方一份 wire contract |
| sidecar 与前端 | `agent/` 中 client/worker/events/prompt/schema 及 tests；`frontend/` 中现有 agent store/API/types/CitationList/MessageList 及 tests | 保留 C 压缩回归；仅最终回答提出引用候选；实际调用 fallback、错误可重试和不可用计数 |
| 主线程 | Trellis/spec、提交、`scripts/smoke/` 集成探针 | 独立检查、真实后端和 sidecar 联调、验证台账、交 D |

首个同步点先固定 `ContextBuild.attempt`、精确来源元数据和 sidecar wire。`context_builder.py` 的预算/持久/同 attempt 重用由同一执行者完成，引用协议执行者只修改调用处，不并行改该文件。若需要共享解析，先确定唯一 helper 所有者，避免各自复制一套授权逻辑。

迁移所有者等双方最终字段/索引约束明确后，基于实际 Alembic head 写一份组合迁移；查询重复旧数据并报告/阻断，不自动去重。运行数据库测试各用自己的隔离临时库；不得同时修改序号或使用同一个 SQLite/端口。

所有任务范围/验收仍以 PRD/design 为准。本表可随实际边界调整，但需先告知受影响执行者并保持唯一文件所有者。
