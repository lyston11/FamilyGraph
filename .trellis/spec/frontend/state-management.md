# 状态管理规范（初始规范 v0）

- Pinia setup store 风格；一个领域一个 store：auth(token/user/PIN_CHANGE_REQUIRED)、spaces(空间列表+当前空间)、graph(成员/关系/位置)、ui(布局模式/弹窗)。
- 服务端数据唯一来源是 store；组件不缓存副本。图数据变更（建档/断连/移动卡片）通过 action 调 API 后更新 store，不做乐观更新（v1 网络环境简单）。
- 缓存失效边界：空间/图数据在切换空间、收到连接变更通知、重新登录时强制刷新；无后台轮询。
- **敏感缓存清理红线**：logout 与 token 失效(401/token_version)时必须清空全部 store + localStorage + 内存图数据，路由守卫兜底跳登录页。
- localStorage 只允许存 refresh token 与 UI 偏好（布局模式），其余一律内存态。

## 空间键控 store 与会话引导（09-01 PersonalFamilyView 前端沉淀）

### Convention: 空间键控 store（space_id + epoch）

**What**：按空间缓存的服务端投影（PersonalFamilyView、household card、notifications、spaceStats）统一使用 `space_id` 键控 Map + 模块级 epoch：
- 读：`forSpace(spaceId)` / `getVisiblePerson(spaceId, userId)` 等已解码查询，不存在「当前视图」全局 getter；
- 写：`load/refresh` 绑定发起时的 epoch，`clearSpace/clear` 递增 epoch，迟到响应一律丢弃；
- 无乐观更新：变更后重读服务端，304/ETag 保留同一快照对象。

**Why**：多空间上下文并存时「全局 current」语义含糊；epoch 是跨空间竞态（旧空间响应回写新空间）的唯一可靠防线。

### Gotcha: 壳层挂载早于路由初始导航解析（P0：/login 无限重载）

**Symptom**：未登录打开任意页面陷入 `/login` 无限整页重载（后端每 ~150ms 一次 401 + bootstrap/status）。

**Cause**：App.vue 在 router 初始导航未解析窗口内 `route.meta` 为空 → `chrome==='blank'` 误判 → AppShell 先于登录页挂载；壳的 `onMounted` 发起需要认证的请求（如 `ensureDefaultSpace` → `GET /spaces`）→ 401 → 会话过期处理器 `location.assign('/login')` 整页重载 → 同一竞态循环。

**Fix（两处，缺一不可）**：
1. 壳层会话引导用 `watch(auth.isLoggedIn, ..., { immediate: true })` 触发，不用 `onMounted`——未登录时零请求，登录/硬刷新恢复完成后触发一次；
2. 会话过期整页跳转在 `pathname === '/login'` 时不再 assign（见 `stores/auth.ts` 的 `sessionExpiredRedirect`）。

**Prevention**：壳/全局组件（尤其 `onMounted`/`immediate` watch）发起的任何请求都必须先判认证态；整页跳转类兜底必须有「已在目标页」短路。

### Convention: ensureDefaultSpace 是登录/硬刷新引导动作，不是页面初始化动作

**What**：`useSpaceContext.ensureDefaultSpace()` 只在登录完成 / 硬刷新（无会话空间上下文）时调用一次。

**Why**：页面（如 PersonProfileView）在会话内到达时重跑默认选择，会把上下文改回「最近 household」，覆盖用户所在的 lineage 上下文 → 目标被误判不可见（走查实测 P1）。会话内到达的页面必须沿用 `spaces.currentSpaceId`；上下文为空才兜底选择，且选择结果与页面语义不符时走安全不可见/空态，不导航回滚。

## 家庭端单主体会话与独立后台隔离（09-04 取代 09-01 的同 SPA 双主体条款）

### Convention: 家庭 auth store 只有 family_user 主体

**What**：`frontend/src/stores/auth.ts` 不包含任何后台主体分支（无 `isSystemAdmin`/`applySystemAdminSession`/`principal_type==='system_admin'` 判定）；`UserOut` 类型不声明 `is_admin`/`platform_role`，`principal_type` 收窄为 `'family_user'`。`sessionExpiredRedirect()` 无参（恒回家庭 `/login`）；`ChangePinView` 完成后回 `/login`；未注册深链（含 `/system-admin`、`/admin`、`/admin-api/*`）统一进入 `NotFoundView` 普通 404——不跳转、不提示其他产品面。

**Why**：09-04 起系统管理员是独立前端应用（`system-admin-frontend/`）+ 独立 API listener（8002 `/admin-api`）+ 独立 JWT 签发域与浏览器存储。家庭 bundle、路由表、类型里出现任何后台主体模型都构成产品边界泄漏（dist 禁止字符串扫描红线）。

**Tests**：`auth.spec.ts`（家庭主体登录/登出/会话过期全链）、`guard.spec.ts`（未注册深链普通 404、无后台路由）、`system-admin-frontend/tests/module-boundary.spec.ts`（后台不 import 家庭代码、仅访问 `/admin-api`、票据仅内存）。

### Convention: 后台前端与家庭前端的会话隔离

**What**：后台前端使用独立 refresh key（`fg.admin.refresh_token`，绝不与家庭 `fg.refresh_token` 共用）、独立 axios 实例（baseURL `/admin-api`）、独立 router/store；access token 与敏感访问票据只存内存，不写 localStorage/sessionStorage；登录响应硬校验 `AdminSessionOut` 白名单字段后才落会话；`password_must_change` 守卫只放行改密页。

**Why**：两个权限域共享任何存储 key 或代码路径，等于把 token 交叉使用面重新打开；后端已按 issuer/audience 物理互拒，前端隔离保证用户永远不会走到那一步。

**Tests**：`system-admin-frontend/tests/auth.store.spec.ts`（独立 key、内存 token、硬校验）、`access-session.store.spec.ts`（票据 TTL/单目标/不持久化）、`router.guard.spec.ts`（未登录/首改密/过期分流）。

### Convention: agent 错误横幅的结构化动作白名单（AgentErrorView.action）

**What**：`stores/agent.ts` 的 partition error 用 `AgentErrorView { code, message, action? }`；`action` 只允许白名单 kind（当前仅 `'open-model-settings'`，由 `providerUnresolvedAction(code, detail)` 从 PROVIDER_UNRESOLVED + detail.reason=cloud_not_allowed 推导）。`ErrorNotice.vue` 按 kind 渲染入口，且权限（`spaces.canManageSpace`）在组件层判定——非管理员渲染纯文案。detail 原始 JSON 不进视图层（与 spec/backend/error-handling.md 的"只映射文案"同口径）。

**Why**：报错要"可行动"而不是纯描述（09-06 事故：文案让用户去模型设置，但不给路径）；同时把"哪些错误带哪些动作"收敛成白名单，避免 detail 形状泄漏进组件。

**Tests**：`ErrorNotice.spec.ts`（管理员见入口且点击直达 `/spaces/{id}/manage?section=models`、非管理员纯文案、无 action 不渲染、STREAM_LOST 文案回退）。
### Convention: 成员授权投影 stale-while-revalidate

`spaces.loadMembers(spaceId)` 重校验同一空间时先发请求、成功后整体替换 `members`；在途期间保留旧成员关系，避免管理员入口因瞬时空数组闪断。失败必须保留旧投影并设置 `membersError`，调用方可展示失败态；路由守卫仍以本次请求结果 fail-closed。跨空间切换清空旧授权上下文，守卫刷新目标空间时传 `setCurrentSpace: false`，不得改写 `currentSpaceId`。
