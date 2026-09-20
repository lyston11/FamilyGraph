# 状态管理规范（初始规范 v0）

- Pinia setup store 风格；一个领域一个 store：auth(token/user/PIN_CHANGE_REQUIRED)、spaces(空间列表+当前空间)、graph(成员/关系/位置)、ui(布局模式/弹窗)。
- 服务端数据唯一来源是 store；组件不缓存副本。图数据变更（建档/断连/移动卡片）通过 action 调 API 后更新 store，不做乐观更新（v1 网络环境简单）。
- 缓存失效边界：空间/图数据在切换空间、收到连接变更通知、重新登录时强制刷新；PFV 的有界轮询例外见下文渐进合同。
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

### Convention: 启动期空间选择单点决策（09-20）

**What**：默认空间与「家族 → household/lineage 落点」的判定集中在
`composables/spaceSelection.ts`（纯函数，可单测）：
`selectDefaultSpaceId`（优先级：最近 household > own/managed household > 第一个
household > 第一个 lineage）、`resolveStartupSpaceId`（先定家族，再按当前路由选
该家族内的 household 或 lineage）、`lineageForSpace` / `householdForLineage` /
`buildFamilyGroups`。`ensureDefaultSpace` 是**唯一**的启动决策点，一次算出最终
空间并只 `switchSpace` 一次。

**Why**：启动期曾有三个写入者竞争 `currentSpaceId`——`spaces.load()` 取列表首项
（服务端按 `created_at` 排序，最新加入的空间不等于用户想先看到的空间）、
`ensureDefaultSpace` 按优先级选 household、`AppShell.syncRouteSpace` 监听
`currentSpaceId` 再把当前家族对齐到路由所需类型。结果是选择器先显示一个空间、
再跳到另一个（09-20 走查实测：朱元璋先显示「李家」再跳「朱氏皇族」）。

**Prevention**：
- `stores/spaces.ts` 的 `load()` 只刷新列表投影，**不挑选默认空间**，也保留既有
  `currentSpaceId`；`currentSpace` getter 严格按 id 解析，不做「列表第一个」兜底。
- `AppShell.syncRouteSpace` 只在**用户显式导航**（`route.name` 变化）时对齐家族
  类型；不得把 `currentSpaceId` 或列表长度当触发源——那会二次切换启动决策的产物。
- 退出空间等需要重新落位的场景显式调用 `selectDefaultSpaceId`，不依赖 `load()` 兜底。
- 规则的任何一处改动必须同步 `spaceSelection.ts`，不在 store getter 或组件里复制第二份推断。

## 家庭端单主体会话与独立后台隔离（09-04 取代 09-01 的同 SPA 双主体条款）

### Convention: 家庭 auth store 只有 family_user 主体

**What**：`frontend/src/stores/auth.ts` 不包含任何后台主体分支（无 `isSystemAdmin`/`applySystemAdminSession`/`principal_type==='system_admin'` 判定）；`UserOut` 类型不声明 `is_admin`/`platform_role`，`principal_type` 收窄为 `'family_user'`。`sessionExpiredRedirect()` 无参（恒回家庭 `/login`）；`ChangePinView` 完成后回 `/login`；未注册深链（含 `/system-admin`、`/admin`、`/admin-api/*`）统一进入 `NotFoundView` 普通 404——不跳转、不提示其他产品面。

**Why**：09-04 起系统管理员是独立前端应用（`system-admin-frontend/`）+ 独立 API listener（8002 `/admin-api`）+ 独立 JWT 签发域与浏览器存储。家庭 bundle、路由表、类型里出现任何后台主体模型都构成产品边界泄漏（dist 禁止字符串扫描红线）。

**Tests**：`auth.spec.ts`（家庭主体登录/登出/会话过期全链）、`guard.spec.ts`（未注册深链普通 404、无后台路由）、`system-admin-frontend/tests/module-boundary.spec.ts`（后台不 import 家庭代码、仅访问 `/admin-api`、票据仅内存）。

### Convention: 后台前端与家庭前端的会话隔离

**What**：后台前端使用独立 refresh key（`fg.admin.refresh_token`，绝不与家庭 `fg.refresh_token` 共用）、独立 axios 实例（baseURL `/admin-api`）、独立 router/store；access token 与敏感访问票据只存内存，不写 localStorage/sessionStorage；登录响应硬校验 `AdminSessionOut` 白名单字段后才落会话；`password_must_change` 守卫只放行改密页。

**Why**：两个权限域共享任何存储 key 或代码路径，等于把 token 交叉使用面重新打开；后端已按 issuer/audience 物理互拒，前端隔离保证用户永远不会走到那一步。

**Tests**：`system-admin-frontend/tests/auth.store.spec.ts`（独立 key、内存 token、硬校验）、`access-session.store.spec.ts`（票据 TTL/单目标/不持久化）、`router.guard.spec.ts`（未登录/首改密/过期分流）。

### Convention: 硬刷新恢复由守卫等待在途轮换，应用挂载晚于首次导航

**What**：后台应用启动时的会话恢复只有一个发起点，且所有恢复入口共用同一笔在途轮换：
- `stores/auth.ts` 的 `runRefresh()` 是启动恢复、路由守卫与 401 重试唯一的轮换实现（`refreshInFlight` 单飞）；`restoreSession()` 复用同一笔（`restoreInFlight`），因此同一次启动至多轮换一次 refresh token。
- `router/index.ts` 的守卫在未登录时**等待**在途恢复（`auth.restoring` 或存在 refresh key 即 `await auth.restoreSession()`），不得跳过等待后按未登录态重定向。
- `main.ts` 在 `app.use(router)` 前发起恢复，并等 `adminRouter.isReady()` 解析后才 `app.mount`（保证 `route.meta` 已就绪）；首次导航 reject（如部署后懒加载 chunk 失效）时仍必须挂载。

**Why**：硬刷新受保护页时守卫若跳过等待，会看到尚未恢复的未登录态并跳 `/login`，恢复成功后也不再重算目标路由，用户就停在登录页；恢复入口若各发一笔，第二笔携带同一份旧 refresh token 会被后端判为重放并撤销全部会话。

**Tests**：`startup.session.spec.ts`（延迟恢复不提前挂载、成功留原深链、失败落登录页并清 key、首改密改派、整链只 1 次 `/auth/refresh`、首次导航失败仍挂载）、`router.guard.spec.ts`（守卫等待在途恢复）、`auth.store.spec.ts`（单飞、restoring 复位、存储不可用不卡死）。

### Convention: agent 错误横幅的结构化动作白名单（AgentErrorView.action）

**What**：`stores/agent.ts` 的 partition error 用 `AgentErrorView { code, message, action? }`；`action` 只允许白名单 kind（当前仅 `'open-model-settings'`，由 `providerUnresolvedAction(code, detail)` 从 PROVIDER_UNRESOLVED + detail.reason=cloud_not_allowed 推导）。`ErrorNotice.vue` 按 kind 渲染入口，且权限（`spaces.canManageSpace`）在组件层判定——非管理员渲染纯文案。detail 原始 JSON 不进视图层（与 spec/backend/error-handling.md 的"只映射文案"同口径）。

**Why**：报错要"可行动"而不是纯描述（09-06 事故：文案让用户去模型设置，但不给路径）；同时把"哪些错误带哪些动作"收敛成白名单，避免 detail 形状泄漏进组件。

**Tests**：`ErrorNotice.spec.ts`（管理员见入口且点击直达 `/spaces/{id}/manage?section=models`、非管理员纯文案、无 action 不渲染、STREAM_LOST 文案回退）。

### Convention: 成员授权投影 stale-while-revalidate

`spaces.loadMembers(spaceId)` 重校验同一空间时先发请求、成功后整体替换 `members`；在途期间保留旧成员关系，避免管理员入口因瞬时空数组闪断。失败必须保留旧投影并设置 `membersError`，调用方可展示失败态；路由守卫仍以本次请求结果 fail-closed。跨空间切换清空旧授权上下文，守卫刷新目标空间时传 `setCurrentSpace: false`，不得改写 `currentSpaceId`。


## 管家建议投影在通知中心的接线（09-15 suggestion-loop / 09-16 auto-apply）

### Convention: 通知中心「待核实」= 通知引用行 ∪ 活跃**可处理**建议投影

**What**：`NotificationsView.vue` 的「待核实」分区同时渲染两个来源：
1. 既有：`classifyNotification` 判为 `verify` 的通知行（`steward_suggestion` 且 `domain_status ∈ {pending, accepted}`）；
2. 新增：`stewardSuggestions.activeForSpace(spaceId)` 的活跃建议投影，覆盖 `notify=False`、**永远不会有通知行**的 kind。

**Why**：`3c2daac` 已让视图 `suggestions.load()`，但 `activeForSpace` 全项目零引用——模板从不渲染其结果。加载了却不渲染，等于"管家没起作用"。

**规则**：
- **`term_preference` 不进「待核实」**（09-16 auto-apply，R4）：称谓优化由管家自动应用到投影，可选「固定为我的叫法/恢复默认叫法」入口在 `KinshipTermPanel`，不是用户待办。`pendingSuggestions` 显式 `.filter((item) => item.kind !== 'term_preference')`；后端投影也把该 kind 的 `pending` 状态降为 `done`（`notifications.py`），因此待办计数同样不含它。历史通知仍按原权限可回看。
- 其余 kind（`relation_proposal` 等）仍按 `SUGGESTION_ACTIVE_STATES` 进入「待核实」。
- **去重范围是全部通知行**，不是只有 `verify` 分区。一条已归入历史（`domain_status=done`）的通知也会让同一 `suggestion_id` 出现在页面上两次。按 `item.suggestion?.suggestion_id` 建 Set，对全部 `page.items` 过滤。
- `activeForSpace` **兑现其名称承诺**：只返回 `state ∈ SUGGESTION_ACTIVE_STATES`（`proposed`/`submitted`，与后端 `app/models/steward_suggestion.py` 同口径）。`superseded`/`expired`/`resolved` 不进「待核实」（后端列表本身是"可回看"语义，过滤在渲染点做，不改后端返回集合）。
- 建议投影行**不携带通知载体**，因此**不标记已读**（不调 `markRead`），只提供「查看详情」；通知行仍走 `openSuggestion`（含已读 + `openSuggestionById`）。两者共用同一个 `SuggestionReviewDialog`。
- 行内取值按 kind 分派：`term_preference` 取 `value.term`（`presentation.summary` 对它是通用文案），`relation_proposal` 用服务端方向化的 `presentation.summary`。与 `KinshipTermPanel` 同口径。
- 空态条件是「通知引用行与建议投影行皆空」。

**Tests**：`views/__tests__/notifications.spec.ts`（投影行渲染与详情接线、`value.term` 优先于通用 summary、同一 id 双来源只渲染一次、已归历史的通知同样参与去重、非活跃状态过滤）、`stores/__tests__/stewardSuggestions.spec.ts`（`activeForSpace` 状态过滤）。

## 渐进 PFV 的版本与展示期限（09-13 / 0045）

完整接口/失败矩阵和测试入口见 [Steward合同 §9](../backend/steward-action-card.md#9-一致快照版本化预览和原子发布0044--0045)。本节只规定浏览器状态所有权。

- `api/personalFamilyView.ts` 严格解码 `pfv-progress-v1`、安全整数 generation/revision、完整 targets/counts。`X-PFV-Validated-At` 优先；存在但非法时拒绝使用，缺少时才兼容单个合法 HTTP Date。有效期换算扣完整 RTT 和1秒精度余量，同时用墙钟/monotonic deadline约束，时钟后调不延长展示。
- `stores/personalFamilyView.ts` 按登录主体×space隔离；epoch、请求序号、generation/revision共同拒绝迟到或倒退。新代完整替换，不能拼旧行；展示到期清内容但保留版本水位，旧响应不能复活。
- 304只续期本次请求对应的相同ETag/版本，且必须包含合法校验时间/有效期；200/304缺元数据不得当成功。401/403/404立即清内容；网络失败仅在尚有效时保留旧画布，有限退避后显示可重试终态。
- `usePersonalFamilyViewPolling` 由树页和资料页共用。preparing/queued短轮询取骨架、building按约1秒推进；本人ready/failed停止高频加载，低频授权保活受期限限制。隐藏/卸载/切空间取消旧请求，回前台先重验。
- 布局依赖topology_revision和布局模式，标签/进度不触发布局或fitView。节点稳定ID保留坐标、视口和选中项。preparing/input_changed/展示到期空窗立即隐藏图内容，仅保留同主体同空间的坐标和布局锚点；新授权骨架或 ready 真空结果到达后才裁剪移除节点。401/403/404、登出或主体切换即使发生于空态期间，也必须清坐标、锚点与视口。布局偏好仅内存按space保存，敏感图数据不写浏览器持久存储。
- 骨架尚无个人称谓时显示“整理中”；failed/unavailable各自有明确文案。本人完成分母不包含其他账号或推测层。
- 断言入口：PFV API/store progress测试、`usePersonalFamilyViewPolling.spec.ts`、树页/资料页测试；真实浏览器脚本验证布局交互，组件stubs不能代替首屏性能证据。
