# 独立系统管理员前端应用：技术设计

## 1. Application boundary

`system-admin-frontend/` 是独立 npm/Vite 项目，不能通过相对路径 import `frontend/src`。它只构建管理员页面和 `/admin-api` client，自己的 HTML、router、Pinia、CSS、测试与 Dockerfile 形成独立模块图。

```text
admin browser :5174 / 127.0.0.1:8081
  └─ admin SPA
       └─ /admin-api → backend admin_app :8002
```

家庭 `frontend/` 不再包含后台路由、SystemAdminShell、SystemAdminView、admin API client 或后台身份类型。家庭未知后台路径在其 Nginx/SPA fallback 统一普通 404。

## 2. Runtime and router

- Vite `server.port=5174`，只代理 `/admin-api` 到 `http://localhost:8002`。
- `createWebHistory()` 只包含 `/login`、`/force-change-password`、`/`、`/space-admins`、`/spaces/:id`、`/operations`、`/agent`、`/audit` 等 admin routes。
- router guard 只读取 admin store：未登录→admin login，`password_must_change`→force change，非 admin token→清会话并回 login；绝不 import family router 或 fallback 到家庭路径。
- session expired 只跳 admin origin 的 `/login`；不使用家庭 `fg.refresh_token` key。
- admin deep links 和 query redirect 只接受当前 admin SPA 内部路径；不接受家庭 URL、绝对外部 URL 或任意 host。

## 3. Stores and API client

```text
adminAuthStore
  accessToken: memory only
  refreshToken: admin-specific localStorage key
  admin: AdminSessionOut
  mustChangePassword
adminAccessStore
  target user/space → 30-minute session in memory only
adminOverviewStore
adminSpaceAdminStore
adminSpaceStore
adminOperationsStore
adminAgentMonitorStore
adminAuditStore
```

`adminApiClient` baseURL 固定 `/admin-api`，只向 admin listener 发请求；request interceptor 附加 admin access token；response interceptor 只处理 admin 401/403，不调用家庭 refresh。敏感详情请求附 `X-Admin-Access-Session`，收到 no-store response 后不写任何持久缓存。

## 4. Page and component structure

```text
AdminApp
 ├─ AdminShell (only admin nav + logout)
 ├─ AdminLoginView
 ├─ ForceChangePasswordView
 ├─ OverviewView
 ├─ SpaceAdminsView
 │   └─ SpaceAdminDetail → ManagedSpaces
 ├─ SpaceDetailView
 │   ├─ MemberProfilePanel
 │   ├─ RelationFactPanel
 │   ├─ OperationsPanel
 │   └─ AgentHealthPanel
 ├─ AnomalyQueueView
 ├─ OperationsView
 ├─ AgentMonitorView
 └─ AccessAuditView
```

主导航顺序和面包屑体现“管理员 → 空间”。空间详情内采用 tabs/折叠面板，避免一次渲染全库；每个 panel 使用分页 API。

## 5. Sensitive access UX

1. 用户点击联系方式/关系敏感详情/其他受限字段；
2. 前端打开理由 modal，理由不写 URL、localStorage 或 sessionStorage；
3. `POST /admin-api/v1/access-sessions` 获取目标绑定 30 分钟 session；
4. 只把 opaque session token 保存在 Pinia 内存；
5. 详情请求附 header；响应 `Cache-Control: no-store`；
6. logout、401、过期、目标切换或 tab 卸载时清除内存票据；
7. 后端 403 时显示重新申请理由，不显示内部错误。

普通空间/申请/状态详情不要求票据，但仍由后端记录读取审计。列表字段按遮罩/白名单显示，后台 UI 不为不存在的联系方式字段预留伪数据。

## 6. Agent monitor

Agent monitor 每 5 秒轮询 overview/runs/jobs；`document.visibilityState !== 'visible'` 时暂停，组件卸载取消 timer。只渲染后端二次脱敏诊断：error code、component、stack location、retry、timing、sanitized summary。任何包含 token/secret/prompt/message/content 的字段在组件层也拒绝渲染。

## 7. Governance action UI

approve/reject 是唯一业务写操作：

- approve 可以不填理由，但必须二次确认；
- reject 必须填写理由且二次确认；
- 完成后显示终态不可改判，刷新列表；
- 不出现重置 PIN、编辑档案、删除/恢复、导出、附件下载或 Agent 控制按钮。

## 8. Security and accessibility

- 错误状态只显示统一安全文案；不显示 token、堆栈原文、密钥或请求正文。
- 所有表单控件有 label、键盘提交/取消和焦点回收；modal 可通过 Escape 关闭（不提交）。
- 375px 下管理员→空间卡片可纵向滚动，表格提供移动端卡片视图；桌面显示分页表格。
- logout 清 admin stores、refresh key 和 access-session memory，并跳 admin `/login`。

## 9. Tests

- 项目模块图/grep 断言不含家庭 import、`/api`、`fg.refresh_token`、家庭 route names 或家庭主体字段。
- Router tests 覆盖未登录、非 admin token、强制改密、session expired 和未知路径普通 404。
- Auth tests 覆盖 admin login、password change、refresh rotation、logout、wrong audience。
- Access session tests 覆盖理由、内存存储、header、目标切换、过期/403/no-store。
- UI tests 覆盖管理员→空间钻取、异常队列、五秒轮询暂停、脱敏错误、approve/reject 和无高风险按钮。
- responsive/accessibility tests 覆盖 375px、键盘和加载/空/错误/no-permission 状态。

## 10. Wrong vs correct

### Wrong

```ts
if (auth.isSystemAdmin) renderSystemAdminShell()
// same family bundle, same router, same localStorage key
```

### Correct

```ts
createAdminApp({ apiBase: '/admin-api', refreshKey: 'fg.admin.refresh_token' })
// separate package/module graph/origin; no family imports
```
