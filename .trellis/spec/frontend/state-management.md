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
