# v1 实际代码基线与 v2 接入点

## 后端可复用

- 身份/JWT：`backend/app/api/deps.py` 的 bearer 解析与认证依赖；`backend/app/models/account.py` 的 token_version/PIN 门禁。
- 关系与空间：`backend/app/services/relation_fsm.py`、`space_fsm.py`；组合事务目前仍部分位于 API 路由。
- 可见性：`backend/app/services/visibility.py` 的 classify/payload 逻辑；v2 需替换“直系结构边自动 full”的旧规则并扩展 space kind/minor/provisional。
- 代管与审计：`backend/app/services/custody.py`、`audit.py`。
- SQLite：`backend/app/db.py` 已设置 foreign_keys/WAL/busy_timeout/synchronous。

## 后端必须先补的边界

- `users.py`、`spaces.py`、`connections.py`、`attachments.py` 仍直接组合 ORM/commit；需抽为 API 与 Agent 共用的 application commands。
- 现有 `visibility.reachable_ids` 从全局关系图 BFS，不能直接作为 Steward 单空间查询。
- 目前没有 SourceFact、DerivedFact、Agent Session/Run/Job/Event、ActionCard、Memory、RAG 或 PolicyService。
- Agent 工具一次只调用领域命令，不接收 SQLAlchemy Session，不挂载 DB，不接受任意查询表达式。

## 前端接入点

- `frontend/src/App.vue` 目前只有 RouterView，是全局 `AssistantLauncher/AssistantPanel` 的正确挂载点。
- `spaces.currentSpaceId` 可提供当前 scope，但切换空间需要同时切换 Session 列表、关闭旧 SSE、清理草稿与缓存。
- 新增独立 agent API/store、SSE transport composable、ActionCard store/component；不可复用仅处理邀请的 `pendingForMe`。
- `auth.clearSession()` 必须扩展以清理 Agent、Message、ActionCard、RAG 查询和 SSE 状态。
- 桌面可参考 Element Plus drawer；移动端需真正全屏而非复用固定 420px 的 ProfileDrawer。

## 部署基线

- 当前 Compose 只有 api + web；v2 新增内部 agent sidecar，浏览器仍不直接访问 agent。
- nginx 继续只暴露 web/API/SSE；agent 网络仅允许 FastAPI 和已批准 Provider/egress。
- agent 容器不挂载 `/data`/SQLite/uploads；Provider secret 不进入前端或模型消息。

## 验证命令基线

- 后端：pytest、mypy。
- 前端：type-check、lint、vitest、build。
- 新 Agent 目录必须提供同等级 type-check/lint/test/build，并纳入 Docker E2E。
