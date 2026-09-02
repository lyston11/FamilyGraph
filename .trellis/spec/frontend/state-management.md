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

## 主体感知会话与系统管理员隔离（09-01 system-admin-governance-routes 前端沉淀）

### Convention: 登录视图写会话前必须硬校验 principal_type

**What**：任何登录视图（家庭 `/login`、系统管理员 `/system-admin/login`）拿到 `/api/auth/login` 响应后，必须先硬校验 `principal_type` 与视图主体一致，才允许调用 auth store 写会话；不一致一律走统一拒绝文案并清理临时状态（`applySystemAdminSession` 对非 `system_admin` 抛错并清状态，见 `stores/auth.ts`）。

**Why**：family_user 凭据建立 system-admin 会话（或反向）等于把两个权限域打通；统一文案且不区分「账号不存在/凭据错误/主体类型」防账号枚举。409 名称歧义（同名家庭账号）也必须按家庭凭据拒绝，不得登入后台。

**Example**：`SystemAdminLoginView.vue` — `pair.user?.principal_type !== 'system_admin'` → 拒绝；提交中/成功态禁止重复提交；redirect 只接受 `getSafeSystemAdminRedirect` 白名单（仅 `/system-admin` 前缀），不信登录响应里的任意 URL。

### Convention: 会话回跳按主体分流，主体快照必须在清会话前取

**What**：
- `sessionExpiredRedirect(wasSystemAdmin)`：system_admin → `/system-admin/login`，family_user → `/login`；
- `ChangePinView`：挂载时快照 principal，完成后 system_admin 回 system-admin 入口，family_user 维持家庭 `/login`。

**Why**：system-admin token 过期被送到家庭 `/login` 会让后台主体误入家庭权限域；反向同理。`clearSession` 之后主体已不可读——所以回跳函数接收主体参数、调用方先快照，这是实测踩过的时序点。refresh 永不改变 principal_type，system-admin token 失效不得降级到家庭登录。

### Convention: SystemAdminShell 零家庭依赖

**What**：系统后台壳不得 import 任何家庭 store（spaces/graph/…）或家庭数据 API；导航只含治理入口；登出走 `auth.logout()`（按主体撤销对应 refresh session 并清系统管理员缓存）后回 `/system-admin/login`。

**Tests**：`SystemAdminShell.spec.ts` 对壳源码做静态断言（无家庭 store/API import）+ 行为断言（无家庭导航、登出跳转）；`auth.spec.ts` 覆盖两种主体的 refresh/logout/session-expired 分流；`guard.spec.ts` 覆盖路由互斥（family_user 进不了后台，system_admin 访问登录页被弹走）。
