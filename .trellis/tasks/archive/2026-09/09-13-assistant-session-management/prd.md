# PRD：助手会话管理重做（标题落库 + 会话列表重设计）

## 背景

家庭助手面板的会话切换器（`frontend/src/components/agent/SessionList.vue`）使用 naive-ui `NSelect`，
会话标题仅在前端内存中生成（`stores/agent.ts` 的 `rememberSessionTitle`，取首条用户消息截断 24 字），
且只在会话被打开过之后才有值。刷新后 titles 清空，未打开过的会话一律回退为
`MM-DD HH:mm` 时间戳，用户无法辨认会话内容；跨设备永远没有标题。

实际缺陷（对照截图与代码确认）：

1. 会话没有服务端持久化的标题与活跃时间，`AgentSessionOut` 只有 `id/space_id/agent_kind/created_at`。
2. `NSelect` 下拉浮层盖住聊天内容，浮层未跟随暗色主题 token（白底突兀）。
3. 会话无重命名、无删除，只能无限累积。
4. `GET /agent/sessions` 无上限，按 `id desc` 排序，无法反映最近活跃。

## 目标

- 会话标题与最近活跃时间由后端落库，跨设备、跨刷新稳定。
- 前端会话切换器重做为面板内的可展开会话列表（不使用浮层），显示标题、相对时间、当前会话高亮。
- 支持会话重命名与删除（含删除守卫）。

## 需求

### R1 会话标题落库（后端）

- `agent_sessions` 新增 `title`（可空，≤120 字符）与 `updated_at`（非空）。
- 首条用户消息创建时自动生成标题：折叠空白后截取 24 字符，超出补 `…`；后续消息不覆盖标题。
- 每次消息创建时刷新 `updated_at`。
- 存量数据迁移：`title` 回填为该会话首条用户消息派生标题（无用户消息则保持 NULL）；
  `updated_at` 回填为该会话最后一条消息时间（无消息则等于 `created_at`）。

### R2 会话列表 API（后端）

- `GET /agent/sessions` 投影增加 `title`、`updated_at`，按 `updated_at desc`（平局按 `id desc`）排序，
  支持 `limit`（默认 50，上限 200）。
- `PATCH /agent/sessions/{id}`：重命名，标题去空白后 1..120 字符；仅本人会话，否则 404。
- `DELETE /agent/sessions/{id}`：仅本人会话，否则 404；会话存在非终态 Run
  （queued/leased/running）时 409（复用 `AGENT_RUN_SESSION_BUSY` 语义）；成功则级联删除消息、Run、事件。

### R3 会话列表重设计（前端）

- 用面板内可展开的会话列表替换 `NSelect`：折叠态显示当前会话标题 + 切换入口；
  展开态为会话条目列表（标题 + 相对时间 + 当前会话高亮）+ 新建入口。
- 列表为面板内普通文档流，不使用弹层，消除遮挡与主题问题。
- 每个条目提供重命名（行内编辑）与删除（两步确认）；删除当前会话后面板回到空状态。
- 标题展示优先级：服务端 `title` → 内存 `titles`（发送首条消息时的乐观值）→ 创建时间回退格式。
- 移动端全屏与桌面抽屉共用同一组件（沿用 PanelContent 约定）。

## 约束

- 不改动 `agent_sessions` 的 scope 三元组不可变约束（DB trigger 仅锁 account_id/space_id/agent_kind，title/updated_at 更新不受影响）。
- 管理员 API（/admin-api）与本改动无关，不新增管理员端点。
- 密钥、token 等内部字段不进入会话投影。
- 兼容性：`title`/`updated_at` 为 additive 字段，旧前端可忽略；API 老调用方不受影响。

## 验收标准

- AC-1：`alembic upgrade head` 在隔离库通过；迁移后存量会话有回填的 title/updated_at。
- AC-2：同一会话发送首条用户消息后，`GET /agent/sessions` 返回派生标题与新的 updated_at；再发消息标题不变、updated_at 前进。
- AC-3：重命名后标题变更；空标题/超长标题 422；他人会话 404。
- AC-4：删除无活跃 Run 的会话成功，其消息/Run/事件级联消失；有非终态 Run 时 409；他人会话 404。
- AC-5：前端刷新页面后，会话列表仍显示服务端标题（不再出现纯时间戳兜底，除非该会话确无标题）。
- AC-6：展开会话列表不再产生浮层遮挡；暗色/纸墨主题下视觉一致（使用既有 token）。
- AC-7：前端可重命名、删除会话；删除当前会话后面板回到「开始新会话」空状态。
- AC-8：backend ruff/mypy/pytest 与 frontend lint/type-check/test/build 全部通过。
