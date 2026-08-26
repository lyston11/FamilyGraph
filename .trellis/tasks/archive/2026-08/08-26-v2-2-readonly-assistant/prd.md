# FamilyGraph V2.2 只读 Assistant：问答、关系解释与全局悬浮 UI

> 依赖：V2.0 Foundation 与 V2.1 Runtime 完成。V2.3 前只解释已确定的结构路径，不生成高级地方称谓。

## Goal

交付第一个用户可用的单空间 Assistant：能基于当前用户真正可见的结构化家谱回答问题、说明证据路径，并通过全局悬浮入口持续流式交互；首版严格只读，以零越权和零业务写入证明 Runtime 边界成立。

## Requirements

### AS-1 单空间 Session

- Session 必须绑定当前 account 与一个 space；用户只能列出/读取自己的 Session。
- 切换空间立即切换 Session 列表并关闭旧 stream，不把消息、草稿、工具结果或 context 带入新空间。
- 一个空间可有多个 Session；Session 标题可自动建议但不成为记忆或人物事实。

### AS-2 只读领域工具

- 工具至少包括：当前空间/本人摘要、列出可见人物、读取可见档案投影、搜索当前 scope、取得两人可见关系路径、解释已确定结构路径。
- 每个工具输入不接受任意 actor/space；scope 由 Run token 注入。输出已经过 VisibilityPolicy，Pi 不接触 masked 原值。
- V2.2 不注册任何业务写工具，也不通过“确认提示”间接写入；未知问题允许回答“不确定/资料不足”。
- 结构化家谱是答案真源；模型必须区分确认事实、派生路径和缺失信息，并在回答中给出自然语言依据。

### AS-3 UI

- `App.vue` 全局挂载悬浮按钮；未登录、强制改 PIN 和无可用空间状态按策略隐藏或禁用。
- 桌面使用可调整/可关闭抽屉，移动端使用全屏面板；支持键盘、焦点回收、屏幕阅读标签和 reduced motion。
- 消息流渲染文本、进行中状态、工具使用摘要、错误/拒绝、重试与 Run 取消；不展示内部 prompt、tool schema、secret 或隐藏字段。
- 空间名称和 scope 始终可见，发送前能确认“正在询问哪个空间”。

### AS-4 SSE 与缓存

- 前端独立 Agent store/API/SSE transport，不依赖 Axios 自动刷新假设；401、token 刷新、断线和 Last-Event-ID 有明确处理。
- Run 进行中刷新页面可恢复；取消只取消本 Run，不删除已持久化消息。
- 登出、账号切换与权限撤销清空 Agent store、关闭 EventSource/fetch stream、删除草稿和未持久化工具结果。

### AS-5 答案安全

- 提示词注入、询问其他空间、要求显示遮罩值、要求“作为管理员”时必须拒绝或只返回当前投影。
- 模型答案不得把推测包装为 SourceFact；无证据路径时明确说明。
- V2.5 前不检索私有 RAG、不自动建立长期记忆；只保留本 Session 消息历史。

## Acceptance Criteria

- [ ] AC-AS1：用户可从任意已登录页面打开悬浮助手，桌面抽屉和 375px 移动全屏核心流程可用且可访问。
- [ ] AC-AS2：同一用户切换两个空间只看到各自 Session/消息/工具投影，用户 B 无法读用户 A Session。
- [ ] AC-AS3：所有注册工具均只读；运行前后业务事实、成员、公开设置和关系表无变化。
- [ ] AC-AS4：关系回答包含可验证路径或明确资料不足；不把自由推测当确认事实。
- [ ] AC-AS5：masked、其他空间、provisional/guest/minor 等对抗问题无法泄露隐藏字段。
- [ ] AC-AS6：页面刷新和 SSE 重连恢复同一 Run；取消、401、Provider timeout、tool failure 有可恢复 UI。
- [ ] AC-AS7：登出/切换账号后 Agent/Message/SSE 缓存为空，浏览器 history/back 不恢复敏感内容。
- [ ] AC-AS8：后端、前端、Agent sidecar 与 E2E 质量门禁全通过。

## Out Of Scope

- 不写 SourceFact、不生成或执行 ActionCard、不做地方称谓完整推断。
- 不启用确认 Memory/RAG 或联网；这些分别属于 V2.5/V2.6。

## Blocking Open Questions

无。
