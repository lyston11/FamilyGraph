# Design — 前端与系统后台设计、接口连通性与发布质量整改

## 1. Boundaries and workstreams

本任务分成五个可独立验证但按顺序集成的工作流：

1. **媒体访问与 API 合同**：家庭端用受控 `fetch/Blob/object URL` 或短期签名机制访问附件；统一错误分类、ETag/304、SSE、multipart 合同。
2. **真实 smoke harness**：在临时 `DATA_DIR` 启动 8000/8001/8002 及两个 Vite 代理，使用合成账号和合成空间验证端到端链路；测试数据、token、provider 内容不得写入报告。
3. **共享设计基线**：保留家庭端 `--fg-*` 作为兼容真源，抽取非业务品牌 token/状态徽章/焦点/间距/玻璃卡片基础层，后台通过兼容映射消费；家庭和后台的认证与路由模块仍完全隔离。
4. **后台响应式与观测**：后台导航在窄屏切换为可折叠菜单；概览/Agent 页面消费已有 ops 状态与安全白名单，只显示计数、状态、时间和安全错误码。
5. **性能与测试质量**：修正动态/静态 import 边界，拆分 Three.js、Agent 和管理页；消除测试环境已知警告，增加媒体认证、错误分类、移动导航和 token 交叉拒绝回归。

不在本任务内重新设计 Steward 后端作业模型；如果观测数据端点尚未存在，只实现前端安全空态和契约测试，并在 notes 中记录依赖任务，不伪造指标。

## 2. Media request design

推荐实现 `fetchAttachmentBlob(attachmentId, signal)`：

- 从家庭 auth store 读取内存 access token，通过同一 refresh executor 处理一次 401 后重试；不得把 token 拼入 URL。
- 以 `URL.createObjectURL(blob)` 提供 `<img>`，组件卸载/换图时 `URL.revokeObjectURL`。
- 仅接受图片响应的安全 MIME/大小上限；非 2xx 映射到 `ApiError`，无权/不存在保持统一安全文案。
- 上传、列表、删除继续走 Axios；为 multipart boundary 和 abort 行为增加契约测试。
- 若部署选择短期签名 URL，签名必须一次性/短 TTL/无长期凭据，且后端仍做授权复核；两种方案不得并存产生旁路。

## 3. API smoke and contract design

测试驱动分三层：

- **前端单元层**：保留现有 decoder/store/view tests，明确只验证本地状态逻辑。
- **listener contract 层**：用 backend TestClient 或临时 listener 验证路由、统一错误 envelope、认证交叉拒绝、字段白名单、ETag/304、SSE 终态和附件鉴权。
- **browser/proxy smoke 层**：使用项目 `scripts/dev-up.sh` 约定启动全栈，在隔离数据目录执行登录、刷新、空间选择、家庭卡、PFV、统计、通知、记忆、Agent SSE、附件和后台敏感详情。服务不可用时输出 blocked，而非 failed。

smoke 报告只保留：测试用例 ID、listener、method/path 模板、状态码、错误码、耗时、SSE 终态、脱敏计数。禁止姓名、PIN、JWT、Cookie、prompt、provider 原始响应和数据库路径。

## 4. Design system and responsive design

- 新增共享 CSS/token 包或共享静态 token 文件，定义品牌色、字体栈、间距、圆角、状态色、焦点环、glass fallback 和 reduced-motion。
- 家庭端现有 `--fg-*` 继续可用；后台 `--ag-*` 通过映射/别名消费共享基础变量，后台专有状态色和权限语义保留在 admin 层。
- 不跨应用共享 Pinia、router、auth store 或 API client；只共享无状态视觉基础。
- 后台导航采用桌面横向菜单 + 移动端菜单按钮/抽屉；表格在 375px 使用卡片或容器级横向滚动，不产生 body 横向滚动。
- 所有新按钮、菜单和表格操作保留可见 focus、ARIA 名称、键盘闭合和 reduced-motion 行为。

## 5. Observability UI design

后台仅消费 `/admin-api/v1` 已批准的元数据或 ops 子任务提供的安全 DTO：

- 状态卡：enabled/disabled/paused/running/degraded/failed。
- 指标卡：core/assist queue depth、oldest queued age、last scan、worker heartbeat、PFV stale/failed、cards created/superseded、safe failure count。
- 列表：job/run ID、space ID、状态、attempt、safe error code、updated_at；空间/人物详情仍需现有 access-session 票据。
- 观测端点失败时显示“观测暂不可用”和重试，不回退到家庭数据或 ORM 原文。
- 重跑入口若已有后端合同则携带 reason + idempotency key 并显示审计结果；没有合同则只保留 disabled/coming-soon 安全说明，不新增未经批准的写接口。

## 6. Compatibility and rollout

1. 先修媒体访问和错误分类，保持现有页面行为。
2. 再引入 token 兼容映射和后台移动导航，桌面 UI 通过截图/组件测试回归。
3. 接入 smoke harness；服务未启动时 CI 标记 blocked，不能把未执行写成通过。
4. 最后处理拆包和观测面板，避免性能改动遮盖功能回归。

发布前必须保持三 listener 拓扑、家庭/admin token 交叉拒绝、家庭端 8000 不出现 admin 路由。任何 token 迁移可回滚到旧 `--ag-*` 消费层；媒体实现保留旧列表/删除 API，不改变附件数据库记录。

## 7. Risks and rollback

- **Blob/object URL 泄漏**：在组件卸载和错误路径统一 revoke；增加重复挂载测试。
- **刷新竞态**：复用现有 single-flight，不在媒体 helper 新建第二套刷新器。
- **共享 token 回归**：先别名迁移，逐页面切换；发现视觉回归可只回滚 admin 映射。
- **smoke 环境污染**：强制临时 DATA_DIR、随机端口/用户前缀和 teardown；启动失败标记环境阻塞。
- **观测过度暴露**：schema 白名单和负向测试先于 UI 渲染；脱敏失败整体拒绝响应。
