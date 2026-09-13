# Design：助手会话管理重做

## 1. 数据模型与迁移

### 列变更（migration 0040_agent_session_title）

`agent_sessions` 新增：

- `title VARCHAR(120) NULL` — 派生或用户重命名后的展示标题；NULL = 尚无用户消息且未重命名。
- `updated_at DATETIME NOT NULL` — 最近一次消息时间；新建会话时等于 `created_at`。

模型（`backend/app/models/agent.py`）同步新增两个 Mapped 字段。

迁移步骤（upgrade）：

1. `op.add_column` 两列（`updated_at` 加 `server_default` 占位后回填再移除 default，或直接 nullable=False + server_default=当前脚本时间统一回填覆盖）。
2. Python 数据回填（逐会话，量级为单账号个人数据，可接受）：
   - `updated_at = COALESCE(max(agent_messages.created_at), created_at)`；
   - `title` = 该会话第一条 `role='user'` 消息 `content_json['text']` 经 `derive_session_title` 派生；无则 NULL。
3. downgrade：drop 两列。

标题派生函数 `derive_session_title(text: str) -> str` 放在 `backend/app/services/`（或紧邻 agent API 的既有工具模块，落地时按目录惯例）：
折叠连续空白为单空格、strip，按 Unicode 码点截断 24 字符，超出补 `…`；与前端 `truncateSessionTitle` 行为对齐（两侧各自实现，纯展示语义）。

### 不变约束

- `trg_agent_sessions_scope_immutable` 的 WHEN 条件只比较 scope 三元组，title/updated_at 更新不被拦截。
- `agent_messages`/`agent_runs`/`agent_run_events` 对 `agent_sessions.id` 均 `ON DELETE CASCADE`；
  连接层 `PRAGMA foreign_keys=ON`（`app/db.py:26`），删会话行即级联清理；`controlled_web` 对 run 为 SET NULL，不受影响。

## 2. API（backend/app/api/agent.py + schemas/agent.py）

### Schema

`AgentSessionOut` 增加 `title: str | None`、`updated_at: datetime`。
新增 `AgentSessionRenameRequest(_Strict)`：`title: str = Field(min_length=1, max_length=120)`（端点内 strip 后复验非空）。

### 端点

- `POST /agent/sessions`：`updated_at=created_at` 一并写入；投影补两字段。
- `GET /agent/sessions`：`order_by(updated_at desc, id desc)`；新增 `limit: int = Query(50, ge=1, le=200)`。
- `POST /sessions/{id}/messages`：写入 user 消息后（含幂等 replay 之外的正常路径）
  `session.updated_at = message.created_at`；`if session.title is None: session.title = derive_session_title(content)`。
  幂等命中 replay 路径不动 session。
- `PATCH /agent/sessions/{session_id}`（新）：404 非本人/不存在；strip 后空则 422；写入 title；返回 `AgentSessionOut`。
- `DELETE /agent/sessions/{session_id}`（新）：404 非本人/不存在；存在
  `status IN ('queued','leased','running')` 的 Run → `raise_api_error(409, AGENT_RUN_SESSION_BUSY, ...)`；
  否则 `db.delete(session)`（级联）+ `audit.write_audit(action="agent_session_deleted", ...)`。

所有权判定沿用列表端点模式：`AgentSession.account_id == account.id`，否则 404（防枚举）。

## 3. 前端

### 类型与 API 封装

- `types/agent.ts`：`AgentSession` += `title: string | null`、`updated_at: string`。
- `api/agent.ts`：`renameAgentSession(sessionId, title)` → PATCH；`deleteAgentSession(sessionId)` → DELETE。

### Store（stores/agent.ts）

- `fetchAgentSessions` 结果直接携带 title，`SessionList` 的标签解析顺序改为
  `session.title ?? partition.titles[id] ?? formatFallbackTitle(session)`。
- 发送首条消息成功后继续写 `partition.titles[sessionId] = truncateSessionTitle(content)`（乐观值，与服务端规则一致）。
- 新增 `renameSession(spaceId, sessionId, title)`：调 API，更新 partition.sessions 中对应项（原地替换保持排序）。
- 新增 `deleteSession(spaceId, sessionId)`：调 API；成功后从 sessions 移除；
  若删除的是 activeSession：关闭进行中流、`forgetActiveRunId`、清空 messages/toolSummaries/run/error/draft、
  `activeSessionId = sessions[0]?.id ?? null`（列表空则 null，PanelContent 回到「开始新会话」分支）。
  409 错误写入 `partition.error`（ErrorNotice 呈现，可重试语义不变）。
- `selectSession` 保持：切换时拉历史，`rememberSessionTitle` 保留为无服务端标题时的兜底。

### SessionList.vue 重做

结构（面板内文档流，无浮层）：

```
toolbar：[当前会话标题(截断) ▾] [新会话]
展开区（v-show，位于 toolbar 下方，占位文档流）：
  ┌ 会话条目 × N ────────────────────┐
  │ 标题（active 高亮 aria-current）  │
  │ 相对时间 · [重命名] [删除]        │
  └──────────────────────────────┘
```

- 折叠态按钮 `data-test="session-toggle"` 显示当前会话标题（服务端 title 优先）+ 会话数徽标。
- 条目 `data-test="session-item"`，点击切换（`emit('select')`）并收起；当前会话条目高亮。
- 重命名：条目行内进入编辑态（`data-test="session-rename-input"`），Enter 提交 / Esc 取消 / blur 提交。
- 删除：两步确认（`data-test="session-item-delete"` → `data-test="session-item-delete-confirm"`）。
- 相对时间：新 util `formatRelativeTime`（<1min 刚刚 / <60min N 分钟前 / <24h N 小时前 / <7d N 天前 / 否则 MM-DD），放组件内或复用既有 util（落地时检查 `src/utils`）。
- 样式全部走既有 `--fg-*` token；列表背景 `--fg-surface-sunken`，active 用 accent 描边/底色。
- 移除 naive-ui NSelect 依赖；保留 `data-test="new-session-btn"`。

## 4. 测试

### 后端（tests/test_agent_browser_api.py 增补）

- 列表投影含 title/updated_at，按 updated_at desc 排序，limit 生效。
- 首条用户消息派生标题；第二条消息标题不变、updated_at 前进；幂等 replay 不改 session。
- PATCH：成功重命名、空标题 422、>120 422、他人会话 404。
- DELETE：级联删除 messages/runs/events（db 计数为 0）、活跃 Run 时 409、他人会话 404、审计记录写入。

### 前端

- `AgentPrimitives.spec.ts`：SessionList 重写——渲染条目/高亮/相对时间、切换、重命名、删除确认流。
- `stores/__tests__/agent.spec.ts`：rename/delete 的 API 调用与 partition 状态变化；删除 active 会话的清场；409 写 error。
- `AssistantPanel.spec.ts`：如引用 session-select 结构需同步。

## 5. 权衡与回滚

- 标题在服务端派生（而非每次请求计算）：一次写入、跨端一致；代价是迁移需要回填。
- 不引入「AI 生成标题」：v1 用首条消息截断，语义与现状一致，后续可作为 additive 增强。
- DELETE 采用 409 守卫而非静默取消 Run：避免隐式取消语义扩散；用户可先取消再删。
- 回滚：迁移 downgrade drop 两列；API/前端均为 additive 或局部替换，revert 提交即可。
