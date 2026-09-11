# 前端与系统后台设计、接口连通性与发布质量整改

## Goal

把家庭前端和系统管理员前端从“静态检查通过、真实链路未证明”的状态提升到可发布状态：敏感媒体能够正常访问，前后端 API 有真实 smoke/契约证据，后台与 FamilyGraph 共享可维护的设计语言，移动端和运维场景有完整反馈，性能与测试门禁可持续执行。

## Background and confirmed evidence

- 家庭前端 type-check、lint、build 通过，512 个 Vitest 测试通过；系统管理员前端对应 76 个测试通过；后端 846 个测试通过、3 个跳过。
- 当前审查时 8000/8001/8002/5173/5174 均未监听，尚无真实 HTTP 连通性证据。
- 家庭 access token 只保存在内存（[frontend/src/stores/auth.ts:14](../../../frontend/src/stores/auth.ts#L14)），但图片以普通 `<img src>` 访问受 Bearer 保护的 raw attachment（[frontend/src/components/member/AttachmentsSection.vue:93](../../../frontend/src/components/member/AttachmentsSection.vue#L93)、[backend/app/api/attachments.py:136](../../../backend/app/api/attachments.py#L136)）。
- 家庭端使用 `--fg-*` 双主题 token（[frontend/src/styles/tokens.ts:1](../../../frontend/src/styles/tokens.ts#L1)），后台独立使用 `--ag-*` 深色 token（[system-admin-frontend/src/styles/main.css:1](../../../system-admin-frontend/src/styles/main.css#L1)），当前属于品牌相似、设计系统分裂。
- 后台移动端只有表格压缩规则（[system-admin-frontend/src/styles/main.css:337](../../../system-admin-frontend/src/styles/main.css#L337)），没有导航折叠方案。
- 家庭端 household/stats/notifications 客户端仍标注“合同占位”，但后端对应接口已经挂载；错误态无法区分真实 404、权限、开关和部署偏斜（[frontend/src/api/household.ts:15](../../../frontend/src/api/household.ts#L15)、[frontend/src/api/spaceStats.ts:7](../../../frontend/src/api/spaceStats.ts#L7)、[frontend/src/api/notifications.ts:18](../../../frontend/src/api/notifications.ts#L18)）。
- 家庭端生产构建报告主 chunk 和 Three.js chunk 过大，并提示动态 import 与静态 import 冲突；测试虽通过仍产生 jsdom/XHR/router 警告。
- 后台 Agent 监控当前只展示 runs/jobs（[system-admin-frontend/src/views/AgentMonitorView.vue:1](../../../system-admin-frontend/src/views/AgentMonitorView.vue#L1)），缺少 worker heartbeat、扫描时间、队列年龄、PFV stale/failed 与辅助降级指标。

## Requirements

### R1. 修复敏感媒体认证链路（P1）

图片缩略图、放大预览和下载必须携带家庭端当前 access token，并在 401 时复用现有 refresh 机制；不得把长期 token 放入 URL、localStorage 或日志。无权访问时显示安全失败态，不泄露附件存在性。

### R2. 建立真实 API smoke 与契约门禁（P1）

覆盖家庭登录/刷新、空间列表与切换、家庭卡、家族视图、统计、通知读写、记忆、Agent SSE、附件和后台登录/刷新/概览/敏感详情/治理写入。测试必须经过真实 listener/反向代理或等价隔离启动环境，记录状态码、错误码、认证边界和 SSE 终态；前端 mock 测试保留但不能替代 smoke。

### R3. 收敛 API 合同和错误语义（P1）

移除已经落地接口的“合同未就绪”占位说明；把 404、403、503、feature disabled、部署偏斜分别映射到可行动的用户文案；对 ETag/304、SSE 断线、multipart、统一错误外壳补充契约测试。

### R4. 统一 FamilyGraph 设计系统并补齐后台响应式（P2）

抽取共享品牌 token、字体、间距、状态徽章、焦点环和玻璃卡片基线；后台保留独立权限域和信息架构，但不再复制一套互相漂移的颜色/间距定义。后台在 375px、768px、桌面宽度下提供可用导航、表格/卡片切换和键盘操作。

### R5. 补齐后台运维可观测性（P1/P2）

在现有后台只读边界内增加核心/辅助队列深度、最老 queued age、最近扫描、worker heartbeat、PFV stale/failed、模型辅助 degraded/disabled/failed、成功/失败计数和安全重试提示；不显示 prompt、家庭内容、token、provider 原始响应。需要重跑时显示受控入口、原因和审计结果。

### R6. 性能和测试质量治理（P2）

拆分家庭端过大的首屏 chunk，确保 Three.js 和 Agent/管理模块按需加载；消除动态/静态 import 冲突。修复 jsdom/XHR/router 测试警告，新增 smoke 失败时可读的诊断输出和最小运行手册。

### R7. 回归与安全边界（P1）

保持家庭端与后台端认证主体、listener、路由和数据隔离；不得把 admin token 引入家庭端，也不得让家庭 token 调用 8002。任何新增观测字段必须经过脱敏和权限白名单。

## Acceptance Criteria

- [ ] 图片缩略图和预览在登录态下真实返回 200；过期 token 能刷新后重试；无权访问返回安全失败态；浏览器网络请求中没有 token query 参数。
- [ ] 两个前端和后端现有门禁继续通过；新增真实 smoke 覆盖 R2 列出的接口，输出可审查的状态码/错误码/终态证据。
- [ ] 真实 API smoke 在服务未启动、代理错误、认证过期和后端 404/403/503 时均给出明确失败原因，而不是统一显示“合同未就绪”。
- [ ] 后台与家庭端共享品牌 token/组件基线；375px 下后台导航可操作且不产生页面级横向滚动；桌面端现有布局不回归。
- [ ] 后台概览或监控页可看到 worker/队列/PFV/辅助状态，且响应中不含 prompt、姓名、家庭内容、token 或 provider 原始错误。
- [ ] 家庭端首屏和 Three.js 代码按需拆分，生产构建不再出现当前主 chunk/动态 import 警告，或有经记录批准的阈值例外。
- [ ] Vitest 不再产生已知 jsdom/XHR/router 警告；新增测试覆盖媒体认证、错误分类、移动后台导航和 token 边界。
- [ ] `git diff` 只包含本任务明确文件；不修改用户已有未相关工作，不启动或归档其他 Trellis 任务。

## Out of scope

- 不重写后端领域模型、权限矩阵、Agent 协议或后台 listener 隔离架构。
- 不新增家庭端全局搜索、自动事实确认、自动入族、任意工具执行或新的管理员数据权限。
- 不把真实 provider 成功伪造成测试成功；外部 provider 不可用时必须报告 partial/degraded。
- 不要求本任务完成部署发布、数据库迁移或提交远程仓库。

## Risks and deferred items

- 真实 provider、Docker/OrbStack 和外部网络可能不可用；smoke 必须区分环境阻塞与产品失败。
- 共享 token 抽取可能影响当前双主题；先保持 `--fg-*` 兼容别名，再逐步迁移后台。
- 性能拆包可能改变初始化时序；必须用 build 产物和关键路由测试回归。

## Open questions

无。用户已明确要求把审查发现全部纳入新的 Trellis 任务；技术选择按 `design.md` 的推荐方案执行，产品范围由本 PRD 固定。
