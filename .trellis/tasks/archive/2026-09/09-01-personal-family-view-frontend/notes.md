# 规划记录

- 用户确认统一家庭用户应用壳：我的家庭、家族树、记忆与知识、统计为一级入口；设置为次级入口；当前空间管理员另有空间管理页。
- 用户确认家庭空间是大卡片，家庭成员不是前端关系白名单，而是在创建账户时选择加入家庭/家族/不加入，未完成时由同一家族空间申请流程兜底。
- 用户确认登录默认进入当前 household 家庭卡；多个 household 独立切换，不合并。
- 用户确认家族空间树直接使用 PersonalFamilyView，去掉列表布局，保留树状和自由画布；背景静态，不要闪烁、漂移或粒子动画。
- 用户确认点击他人头像进入独立只读公示个人页；关系/加入空间等操作继续使用既有申请、确认和 ActionCard 流程。
- 用户确认记忆页面分待确认、私有、家庭共享、家族共享、检索引用；默认优先待确认，否则私有。
- 用户确认普通家庭端与系统管理员后台完全分离，系统管理员使用独立登录接口和页面壳。

## 当前实现阻塞/跨任务依赖

完整家庭卡、通知中心、空间限定统计和系统管理员登录合同当前需要服务端/系统管理员任务配合。前端不得使用 `/users` 全局列表、旧 graph 或本地关系推导临时拼装；若合同未落地，先固定 runtime guard、fixture 和 contract tests。

## 前端客户端合同占位（待服务端任务对齐）

以下三个端点为前端 Phase 1（2026-09-01）约定的客户端合同，对应 api 文件头部均标有
`BLOCKER: 服务端合同未落地`。后端任务落地时请按此对齐；对齐前前端仅以
fixture/decoder/store 测试驱动，不改用 `/users` 全局列表、旧 `/api/graph/me` 或旧
members 列表拼装任何投影。共享类型集中在 `frontend/src/types/api.ts`。

### 1. HouseholdCard — `GET /household-card?space_id=<id>`（frontend/src/api/household.ts）

- 支持 `If-None-Match`/ETag；304 时前端复用上一份安全快照。
- 载荷 `HouseholdCardData`：
  - `space_id: number`、`space_kind: 'household'`、`space_name: string`
  - `view_version: number`、`computed_at: string | null`
  - `viewer: PersonalFamilyViewDisplay`（本人授权 profile display，字段级 masked 哨兵 `{__masked__: true}`）
  - `members: [{ user_id: number, display: PersonalFamilyViewDisplay, household_label: string, visibility_level: 'self_private'|'household_detail'|'lineage_summary' }]`
    —— 仅 confirmed active household 成员；pending/removed/普通亲属/`none` 不出现，无隐藏成员数量
  - `allowed_actions: { can_invite_members: boolean, can_create_household: boolean, empty_state_hint: string | null }`
    —— 空状态/邀请/创建入口资格由服务端给出
- 不包含 lineage 节点数组或其他空间资料；成员不得由前端经 `/users`/members 合并。

### 2. Notifications — `GET /notifications?space_id=<id>`、`POST /notifications/{id}/read`、`POST /notifications/read-all`（frontend/src/api/notifications.ts）

- 列表载荷 `NotificationsPage`：`{ space_id: number, unread_count: number, items: NotificationItem[] }`，支持 If-None-Match/304。
- `NotificationItem`：
  `{ id, space_id, kind: 'action_card'|'space_membership'|'bridge'|'relation', payload: { title: string, summary: Maskable<string>|null, actor_name: Maskable<string>|null, space_name: Maskable<string>|null }, domain_status: 'pending'|'active'|'accepted'|'rejected'|'cancelled'|'revoked'|'expired'|'withdrawn'|'removed'|'done', action_card: { card_id: number, revision: number } | null, created_at: string, read_at: string | null }`
  - `kind='action_card'` 的通知必须携带 `action_card` 引用；ActionCard 处理状态以
    ActionCard 服务端/store 为准。
  - 已读（read_at/unread_count）、ActionCard revision、领域状态（domain_status）三者严格分离。
- `POST /notifications/{id}/read` → `{ id: number, read_at: string }`；仅置已读，不得触发任何领域状态变更。
- `POST /notifications/read-all`，body `{ space_id: number }` → `{ space_id: number, marked_count: number }`。
- 通知按账号 + space_id 过滤；Bridge 管理员通知不得携带敏感家庭数据或管理操作。

### 3. Scoped stats — `GET /stats?space_id=<id>`（frontend/src/api/spaceStats.ts）

- 现有 `/stats` 的向后兼容查询参数扩展；Phase 5 起 `frontend/src/api/stats.ts`
  旧无空间合同已删除（StatsView 切换到 spaceStats store 后无任何消费方，
  全局引用检索确认），统计页面不回退旧 `/stats` 语义。支持 If-None-Match/304。
- 载荷 `SpaceStatsData`：
  `{ space_id: number, space_kind: 'household'|'lineage', status: 'never_computed'|'queued'|'running'|'current'|'stale'|'failed', view_version: number | null, node_count: number, edge_count: number, member_count: number, relation_distribution: [{ dir_class: 'elder'|'younger'|'peer'|'spouse', count: number }], pending_action_cards: number, pending_memberships: number, computed_at: string | null, stale_reason: string | null }`
- 聚合为服务端授权口径：隐藏对象/未授权分支不计入，前端不从节点数组推导统计、不做跨空间总计。

## Phase 2（2026-09-01）实现记录

### GlobalSearch 从应用壳移除

- 移除原因：新壳导航不含全局搜索；旧全局搜索走旧 `/search` 合同，按 viewer 全域
  匹配与"当前空间是唯一页面授权上下文"的授权边界冲突（旧搜索可命中无共同空间
  关联的人员，越出 PersonalFamilyView/household 投影口径），后续按空间内检索
  另行设计。
- `AppShell.vue` 不再挂载 `GlobalSearch`；组件文件
  `frontend/src/components/common/GlobalSearch.vue` 与其 spec 暂保留，因为旧
  `FamilySpaceView.vue`（Phase 3 将整体改造为 FamilyTreeView）仍引用它；Phase 3
  改造 FamilySpaceView 时一并删除该组件与其旧 `/search` 合同。

### 空间切换事务实现位置

- 协调器：`frontend/src/composables/useSpaceContext.ts`（design.md §3.2）。
- 固定顺序：①校验目标空间在服务端 `GET /spaces` 列表（空列表补拉一次）→
  ②递增切换 epoch 并更新 `spaces.currentSpaceId`（旧请求结果即刻失效）→
  ③清理旧空间敏感缓存（personalFamilyView / household / spaceStats /
  notifications 的 `clearSpace`，memory/RAG、actionCards、kinship、agent 的
  `resetForSpace`）→ ④按空间类型加载新投影（household→household card；
  lineage→PersonalFamilyView；通知徽标并行，全部失败进安全失败态不抛出）→
  ⑤重算当前空间管理员入口（spaces getters 响应式）与默认页面目标并导航
  （household→`/`，lineage→`/family-tree`）。
- 默认空间选择优先级（`selectDefaultSpaceId` 纯函数）：会话内最近使用的
  household（ui store `recentHouseholdId`，仅内存）> own（`owner_id`）/managed
  （active `space_admin` 成员关系）household > 第一个 household > 第一个
  lineage；完全没有空间返回 `none`，由家庭卡空状态走现有创建空间引导，不静默创建。
- 登出 / 401 全量清理仍由 `auth.clearSession` 负责（Phase 1 已含 household/
  notifications/spaceStats），本模块只处理空间切换粒度。

### 路由表（Phase 2 前后对照）

| 路由 | Phase 2 前 | Phase 2 后 |
|---|---|---|
| `/` | name `family-space` → FamilySpaceView | name `home` → HouseholdCardView（Phase 2 占位页） |
| `/family-tree` | 不存在 | name `family-space` → FamilyTreeView（Phase 2 占位页） |
| `/people/:userId` | 不存在 | name `person-profile` → PersonProfileView（Phase 2 占位页） |
| `/notifications` | 不存在 | name `notifications` → NotificationsView（Phase 2 占位页） |
| `/home` | name `home` → HomeView（旧成员混合页） | 显式 redirect → `/`，不再挂载（HomeView 文件保留待 Phase 3 抽取） |
| 其余（login/onboarding/force-change-pin/identity-setup/stats/system-admin/admin redirect/spaces/:spaceId/manage/memory/settings/404） | 不变 | 不变；守卫内 `family-space` fallback 语义随 name 变为家族树入口 |

### Assistant 入口

- 壳顶部的 Assistant 面板本期只保留 disabled 入口占位（依赖 Agent Runtime
  任务交付），不实现面板；空间切换/登出对 Assistant context 的清理通过
  `agent.resetForSpace` / `agent.clear` 占位对齐。

## Phase 3（2026-09-01）实现记录：家庭卡与家族树替换

### HomeView 流程保全审计结论（implement.md Phase 3 第 1 条）

删除 `views/HomeView.vue` 前，对其中 990 行做了领域流程清点：

- **已有可复用组件，原样保留（未编辑）**：`MemberCreateWizard.vue`（成员创建，
  WIP 保护）、`AddRelationDialog.vue`（关系请求）、`OneTimePinDialog.vue`
  （一次性 PIN）、`ProfileDrawer.vue`（档案抽屉）、`SpaceGovernancePanel.vue` /
  `SpaceGovernanceDialog.vue`（空间治理）、`AttachmentsSection.vue` /
  `DataRightsPanel.vue` / `PendingProfileRefs.vue`。MemberCreateWizard /
  AddRelationDialog 旧挂载点随 HomeView 删除，组件与其测试保留，等待 Phase 4
  （个人页 ActionCard 入口）/ Phase 6（空间管理侧栏）接线。
- **HomeView 内联逻辑 → 新抽出组件（components/member/）**：
  - `SpaceCreateDialog.vue`：创建空间弹窗（POST /spaces，household/lineage
    kind 可选），由家庭卡空状态「创建家庭空间」入口使用；
  - `InviteMemberDialog.vue`：邀请成员弹窗（按名前缀搜索 + spaces.invite），
    由家庭卡空状态「邀请家人」入口使用；授权仍以 spaces.canInvite 判定；
  - `SpaceManagerApplicationPanel.vue`：申请接手族谱空间 + 我的管理申请状态行
    + 原管理员交接工单（consent），自管加载，嵌入 `SpaceManagementView.vue`
    「管理员申请与交接」卡片。旧 home.spec 中申请/工单用例迁移至
    `SpaceManagerApplicationPanel.spec.ts`。
- **已删除**：`views/HomeView.vue`、`views/FamilySpaceView.vue`、
  `components/common/GlobalSearch.vue` 及三者 spec（`home.spec.ts`、
  `family-space.spec.ts`、`GlobalSearch.spec.ts`）。
- `composables/useLayout.ts`（旧画布三布局）已无页面消费，但按「不扩权删除」
  原则与 `useLayout.spec.ts` 一并保留，供后续流程复用/下线决策。

### HouseholdCardView（design.md §5.1）实现要点

- 右栏家庭成员**只消费 household store**（`GET /household-card` BLOCKER 占位端点）；
  加载失败/合同未就绪（含 404）→ 「家庭卡服务合同未就绪」状态面板 + 重新加载，
  绝不回退 members store、/users 或本地拼接（测试含红线断言）；
- 左栏本人资料用 auth store 本人 profile（姓名 + profile_status 徽章 + 姓字纸牌），
  编辑资料/隐私与公示设置为 `settings` 路由跳转，不做行内编辑；
- 成员卡片：名字 + 服务端 `household_label` + 字段级 masked（MaskedField）+
  visibility 层级文字；网格/列表切换（组件内 UI state）；点击 →
  `/people/:userId`，本人卡片点击不跳转（design.md §2「点击自己的节点不进入该路由」）；
- 退出按钮（左上角）→ useSpaceContext 切到第一个可用 lineage（switchSpace 事务
  自动导航 /family-tree）；无可用 lineage → 安全提示卡保留在家庭卡；
- 空 household：保留卡片骨架，按服务端 `allowed_actions` 显示邀请/创建入口
  （SpaceCreateDialog / InviteMemberDialog），绝不静默创建；
- 当前空间不是 household 时显示安全上下文面板（household 空间切换按钮），
  不渲染任何家庭卡内容。

### FamilyTreeView（design.md §5.2）数据流与状态机要点

- 数据源**只用 personalFamilyView store**（按当前 lineage space_id load/refresh，
  304 复用同一快照）；测试断言 `fetchMyGraph`（旧 graph）零调用；
- Vue Flow 数据构造在页面级纯函数 view-model `composables/useFamilyTreeCanvas.ts`：
  nodes 从已解码 `PersonalFamilyViewNode` 构造（lineage_summary 标记不可展开、
  无展开按钮、无家庭卡入口；`none` 已被 decoder 丢弃）、edges 从
  `PersonalFamilyViewEdge` 构造；树状布局按服务端 path 步骤 direction
  （up/down/sym）做几何世代分带，长辈在上/晚辈在下/同辈同行，同带按 user_id
  升序居中排开；自由画布为确定性环形摆位——**旧 graph 的 node_positions 不读、
  不混入**；
- `MemberNode.vue` 改为纯展示（props 收已解码 display + 可见性层级 + isSelf +
  term；emit select；不发请求、不读路由）；自己强调 + `self_private` /
  `household_detail` / `lineage_summary` / masked 全部 icon+文字
  （MaskedField）表达；
- 交互：点自己 → `/`（家庭卡）；点他人 → `/people/:userId`；点边 →
  `RelationshipDetailPanel.vue` 只读面板（称谓、主路径、≤3 条替代路径、
  path_class/concept_code 安全来源摘要、view_version/computed_at；
  申请更正/查看待办为 disabled 占位留 Phase 4；面板为覆盖层，画布位置缩放不变）；
- 状态机 UI：`never_computed/queued/running` 且无数据 → 状态面板（不画布）；
  `current` → 画布；`stale`/`failed` 有快照 → 画布 + 版本/时间/原因标注；
  `failed` 无快照 → 失败状态面板；403/404/网络错误 → 安全失败状态（不显示
  空间名/ID）；`truncated=true` → 截断提示 + 已加载数量；
- 画布工具只保留：树状（默认）/自由画布切换（组件内 UI state，不进 localStorage，
  避免旧 `fg.layout` 列表值复燃）、缩放（Controls）、适应画布、回到自己、
  重新加载、图例；**列表布局入口已删除**；
- 非 lineage 上下文（如选中 household 时直达 /family-tree）显示安全上下文
  面板与 lineage 空间切换按钮，不渲染投影内容。

### 测试与验证

- 新增 spec：`views/__tests__/household-card.spec.ts`（10 例）、
  `views/__tests__/family-tree.spec.ts`（9 例）、
  `components/canvas/__tests__/RelationshipDetailPanel.spec.ts`（8 例）、
  `composables/__tests__/useFamilyTreeCanvas.spec.ts`（8 例）、
  `components/member/__tests__/SpaceManagerApplicationPanel.spec.ts`（5 例，自
  旧 home.spec 迁移）、`SpaceCreateDialog.spec.ts`（2 例）、
  `InviteMemberDialog.spec.ts`（2 例）；`MemberNode.spec.ts` 按新合同重写（7 例）。
- 门禁（2026-09-01，frontend/）：`npm run type-check` 零错误；`npm run lint`
  零告警；`npm test` 54 文件 386 用例全绿；`npm run build` 成功（仅存量
  index chunk >500kB 警告）；`git diff --check` 干净。
- 375px paper/modern 人工走查按计划留待 Phase 7 一并执行并记录。

## Phase 4（2026-09-01）实现记录：个人页、关系说明和 Bridge 待办入口

### PersonProfileView（design.md §5.3 / PRD §2.4）实现要点

- 只接受路由参数 `userId`；当前 lineage 的 space_id 来自 spaces/useSpaceContext
  会话上下文（页面不读任何 viewer/root 参数，也不把敏感节点写进 URL）。
- 直达/刷新流程：`ensureProfile()` 先在 spaces 列表缺失时补拉（失败 → 安全失败
  态）→ `spaceContext.ensureDefaultSpace()`（不导航）建立会话空间上下文 → 非
  lineage 上下文直接安全不可见（不为该上下文加载投影）→ `pfv.load(spaceId)` →
  `getVisiblePerson(spaceId, userId)` 快照内命中；绝不调用 `/users` 或按 userId
  的宽泛用户详情接口。
- 安全状态矩阵：
  | 场景 | 页面状态 | 呈现 |
  |---|---|---|
  | 快照/上下文建立中 | loading | NSpin，无数据 |
  | 快照加载网络/未知错误 | error（`profile-load-error`） | 可重试（重走 PFV 端点），不显示空间名/ID |
  | 目标不在快照中（不存在/不可见/被撤权） | unavailable（`profile-unavailable`） | 「对方不可见或不存在」，无目标 ID/空间名/路径长度/任何数量 |
  | 投影端点 403/404 | unavailable（同一形状合并） | 与上完全同形状，防存在性探测 |
  | 目标命中 | ready | 只读公示内容 |
  | 会话内快照刷新后目标被撤权 | computed 回落 → unavailable | 响应式回收，不留旧授权节点 |
- 只读内容：身份头部（姓字纸牌头像 + 姓名 + 可见性层级 icon+文字：仅本人可见/
  家庭详情可见/族谱摘要·不可展开）；公示字段行（gender/birth/death/bio/
  privacy_mode/claim_status，masked 走 MaskedField，明文枚举用与 ProfileDrawer
  一致的中文文案：移交本人/永久管理、已确档/待确档）；关系上下文（快照 edges 中
  与目标相邻的边 → 称谓 + path_class 文案行，点击打开只读 RelationshipDetailPanel
  覆盖层并回页首保证可见）；无相邻边显示安全空文案。
- ActionCard：进入 ready 后经 actionCards store `ensureLoaded`（入口降级 403/503
  静默无按钮）静默加载；仅当存在引用该目标的非终态卡时提供「查看待办」按钮 →
  `/notifications`；不提供修改对方资料/建立关系/加入空间/查看对方家庭/扩大权限
  的任何按钮。
- 点击自己（userId === auth.user.id）：页面级 watch immediate → `router.replace`
  回 `/`（家庭卡）；`ensureProfile` 对 self/null 目标直接 return，不发起任何加载
  （瞬态路由参数变化也不会触发投影请求）。
- 返回家族树：仅 `router.push({name:'family-space'})`，不做 switchSpace，同一
  lineage 空间上下文由 spaces store 会话态保持；硬刷新时页面经 ensureDefaultSpace
  重建默认上下文。

### RelationshipDetailPanel 完善（Phase 3 初版基础上）

- 合同补齐：替代路径 >3 条截断渲染并显示「仅显示前 3 条」提示
  （`relation-alt-paths-truncated`）；新增事实状态字段（inclusion_reason_code
  文字化，`confirmed_path` → 已确认的关系事实，未知码回退安全文案，不渲染原始码）；
  保留称谓/主路径/path_class+concept_code 安全来源摘要/投影版本与更新时间；
  summary（lineage_summary）目标不可展开，面板无任何展开控件。
- 「申请更正」「查看待办」接线为安全跳转：只 emit `request-correction` /
  `view-todos`，由 FamilyTreeView / PersonProfileView 导航到 `/notifications`
  待办区；面板保持只读，绝不产生 SourceFact 写操作。
- path_class / fact_state 文案抽到共享模块
  `components/canvas/relationshipDisplay.ts`（面板与公示页单一来源，避免漂移）。

### Bridge 待办入口（PRD §2.4/§2.6 边界）

- Bridge pending 只在通知/待办处理：PersonProfileView 与 RelationshipDetailPanel
  均不渲染 approve/reject/consent/revoke 控件（测试以文案与 data-test 正则双重
  断言固定；空间管理员对跨 LineageSpace bridge 只有通知查看权的约束以测试注释 +
  断言形式固定，完整通知页在 Phase 5）。
- `stores/personalFamilyView.ts` 新增 `reloadAfterBridgeChange(spaceId)`：内部
  强制 refresh（跳过 ETag，服务端重新授权复核），供 Phase 5 NotificationsView
  在 bridge pending → active 后触发家族树/公示页重载；只读重载，无任何 Bridge
  写操作。窄测试断言其绕过 ETag 且仅产生 GET 投影请求。

### 测试与验证

- 新增 spec：`views/__tests__/person-profile.spec.ts`（16 例：直达/硬刷新建立
  上下文、不可见目标不泄漏 ID/空间名、网络失败可重试、403/404 同形状、非 lineage
  上下文不加载投影、自己重定向家庭卡且零投影请求、返回家族树保持上下文、masked/
  明文/可见性 icon+文字、关系上下文与面板打开、无相邻边空文案、ActionCard 跳转与
  无关时不渲染、只读无写按钮、Bridge 无操作控件）。
- 更新 spec：`RelationshipDetailPanel.spec.ts`（截断提示、事实状态、跳转事件 +
  无 Bridge 控件断言）、`family-tree.spec.ts`（面板「查看待办」→ notifications
  跳转）、`stores/__tests__/personalFamilyView.spec.ts`（reloadAfterBridgeChange
  窄测试）。
- 门禁（2026-09-01，frontend/）：`npm run type-check` 零错误；`npm run lint`
  零告警；`npm test` 55 文件 406 用例全绿；`npm run build` 成功（仅存量 index
  chunk >500kB 警告）；`git diff --check` 干净；新改文件 grep 无 v-html、无硬编码
  色值、无 axios 直连。
- 375px paper/modern 人工走查仍按计划留待 Phase 7 一并执行并记录。

## Phase 5（2026-09-02）实现记录：记忆、通知、统计和设置

### MemoryView 五标签改造（PRD §2.5 / design §5.4）

- 结构拆分结论：`MemoryManager.vue` 重排为五标签容器（`NTabs`，移动端 ≤600px
  以 CSS 把标签栏变分段控制器外观，无新颜色），从原单容器拆出四个子组件：
  - `MemoryCandidateConfirmDialog.vue`：候选确认弹层。确认前完整展示原话、摘要、
    用途、敏感等级（icon+文字徽章）、目标 scope 下拉与随 scope 联动的隐私影响说明；
    只提供「确认保存 / 忽略（拒绝）/ 稍后处理（仅关闭）」三种动作，无绕过审计的
    直接发布；每次打开重置为最小披露默认 scope=private；高敏感（high/local_required）
    候选共享选项 fail-closed 置灰并保留可见降级文案。
  - `MemoryEditorDialog.vue`：新增/保存共用编辑器。私有「新增」与检索结果「保存」
    都只提交 `POST /memory-candidates`（suggested_scope 默认 private），进入待确认
    流程后才由用户明确 scope；不提供直接创建可检索记忆的入口。
  - `MemoryCardItem.vue`：正式记忆卡（private/household/lineage 三标签共用）。
    scope、敏感等级、修订、保留期限以 icon+文字呈现；撤销/删除只 emit，由容器经
    memory store 写服务端并重读。
  - `MemoryRagPanel.vue`：检索与引用标签。只读展示 `citation_handle`、source
    （source_type · source_id）、scope、revision、索引版本；「保存为候选」打开
    共用编辑器预填原文，绝不直接写记忆。
- 默认标签规则：候选加载完成后决定——有待确认候选默认「待确认」，否则默认
  「我的私有记忆」（MemoryManager `load()` 初始化 `activeTab`）。
- 候选/正式记忆/检索结果视觉状态分离：候选 = proposed 左缘线 + 「候选 · 未进入
  检索」icon+文字徽章；正式记忆 = neutral/confirmed/accent scope 徽章；检索结果 =
  confirmed 左缘线 + 对勾徽章（不只靠颜色）。
- 所有写入/撤销/删除/确认完成后由 memory store `refreshAfterMutation` /
  `loadCandidates` 重读服务端状态，无乐观本地副本（store 测试 + 组件测试断言重拉）。
- scope 标签是展示层过滤：private/共享列表都来自服务端已返回数据，按
  `scope`/`space_id` 字段过滤展示，不做前端授权推导。
- **memory API 空间语义差异记录**：现有 `api/memory.ts` 合同已带空间语义
  （`GET /memories?space_id=`、`GET /rag/search?space_id=`），未发明新参数；
  候选列表（`GET /memory-candidates`）是账号级而非空间级，沿用既有合同展示
  （待确认标签不按空间过滤），未做任何前端空间推导。
- **RAG trust 字段差异记录**：PRD §2.5 要求检索结果展示 trust，但现有
  `GET /rag/search` 合同（`MemoryCitation`）无独立 trust 字段（只有
  sensitivity/revision/index_version）。按红线不发明参数：面板展示
  sensitivity 标签代替，缺失差异在此记录，待服务端合同扩展 trust 后补展示。
- 空间切换清理：`useSpaceContext.clearSpaceCaches` 调 `memory.resetForSpace`
  （清该空间共享记忆 + RAG 结果分区，private 记忆是账号级不清）；
  `useSpaceContext.spec.ts` 事务顺序用例对 `memory.resetForSpace`、
  `spaceStats.clearSpace`、`notifications.clearSpace` 均有断言（清理发生在
  context 已切换之后，且不误清新空间）。

### NotificationsView（PRD §2.6 / design §5.4）

- 三分区归类纯函数 `types/notifications.ts:classifyNotifications`（可单测）：
  - 待我处理：`kind='action_card'` 且 `domain_status='pending'`；
  - 已完成·历史：`read_at` 有值且 `domain_status` 非 pending（领域终态）；
  - 通知：其余（含未读的领域终态告知）。
- **通知页与 ActionCard 状态分离实现**：打开通知 = `notifications.markRead`
  （服务端 `POST /notifications/{id}/read` 命令 + 成功后重读列表，非乐观 UI）；
  「全部标记已读」= `markAllRead` 同理。两者都不触碰 actionCards store，也不本地
  改写 `domain_status`/ActionCard 引用（store 测试断言 ActionCard 引用仅随服务端
  载荷变化；组件测试断言 view/accept/dismiss/execute 四个 ActionCard API 零调用）。
- 「去处理」：打开受控 `ActionCardInbox`（`v-model:opened`）+ `actionCards.ensureLoaded`，
  进入既有 ActionCard 流程（accept/dismiss/execute 都在卡片组件内）；通知页自身
  不执行任何卡片动作。
- 领域终态展示为状态标签（`NOTIFICATION_DOMAIN_STATUS_LABELS/BADGES`，icon 徽章）；
  bridge active 通知出现时 watch 触发 `personalFamilyView.reloadAfterBridgeChange`
  （每次出现只触发一次，只读 GET 投影，无 Bridge 写操作）。
- 空间管理员对 bridge 通知只有查看权：页面不渲染任何 approve/reject/consent/
  revoke 控件（组件测试以 data-test 与禁用文案双重断言固定）。
- BLOCKER 端点 404 → `notifications-contract-unready` 安静安全态（无假数据、无
  错误横幅）；非 404 → 可解释失败态 + 重试。
- 顶部未读入口联动：AppShell 与通知页消费同一 notifications store
  （`unreadCountOf`）；AppShell watch currentSpaceId 拉取（404 安静降级），
  通知页 markRead 重读后两处徽标同步更新。

### StatsView 空间化（PRD §2.6）

- 数据流：`spaces.currentSpaceId` → spaceStats store `load(spaceId)`（带 ETag/
  304、epoch）→ `GET /stats?space_id=` 服务端授权聚合 → 页面渲染。页面不发请求，
  不从 PersonalFamilyView 节点数组、graph store 或本地数组推导任何统计。
- 展示：授权节点/关系/成员摘要卡、关系分布（服务端 dir_class 切片）、待确认事项
  （pending_action_cards / pending_memberships）、6 态状态（never_computed/queued/
  running/failed → 统一状态面板不显示数字；stale → 数据 + 明确过期标注；current →
  正常）、view_version/computed_at。household 显示「家庭授权聚合」、lineage 显示
  「当前家族视图聚合」口径标签；不做跨空间总计（测试断言两空间数字之和不出现在
  页面文本）。
- BLOCKER 端点 404 → 「统计服务合同未就绪」安全态面板，不回退旧无空间 `/stats`
  合同。
- 旧 `frontend/src/api/stats.ts` 已删除：StatsView 切换到 spaceStats store 后
  全局引用检索确认无任何消费方，其测试文件一并删除。

### SettingsView 重排（design.md §5.4）

- 四分区：个人资料（auth store 本人信息 + 改名）、隐私与公示（DisclosureMatrix）、
  账号与安全（ChangePinForm + DataRightsPanel + 登出）、显示与无障碍（paper/modern
  双主题预览卡切换，消费 `stores/ui.setTheme`，data-theme/localStorage/aria-pressed
  同步）。复用现有组件，无新增授权行为；登出、账号信息沿用 auth store。
- **ProfileDrawer 未进入全局设置的差异记录**：ProfileDrawer 依赖旧 `/users`
  members 合同（Phase 3 已从家庭卡移除其挂载点），「编辑资料」入口由家庭卡与
  现有资料流程承担；全局设置只保留改名（`PUT /me/name`，auth store），避免设置页
  重新引入旧宽接口依赖。
- 空间管理不放进全局设置：无 space-management 入口（组件测试断言无相关
  data-test 与文案）；空间管理由 AppShell 当前空间 `space_admin` 上下文入口承担。

### 测试与验证（Phase 5）

- 新增/更新 spec：
  - `components/memory/__tests__/MemoryManager.spec.ts`（9 例）：五标签渲染与
    默认规则（有候选→待确认/无候选→私有）、候选确认后重新拉取候选+记忆（无乐观
    副本）、确认弹层原话/摘要/用途/敏感等级/隐私影响、private 默认 scope 提交、
    共享确认交互（household:<space_id>）、高敏感共享置灰 fail-closed、空间切换
    清理旧分区并拉取新空间、检索只读 + 保存仅新建候选（断言 confirm 零调用）、
    CitationList 引用投影。
  - `views/__tests__/notifications.spec.ts`（8 例）：三分区归类、打开只已读且
    ActionCard 四接口零调用、全部已读不改 ActionCard、去处理打开既有 Inbox、
    bridge active 触发 reloadAfterBridgeChange（仅 1 次 GET 投影）、404 安静降级、
    非 404 可重试失败态、管理员对 bridge 通知无任何操作控件。
  - `views/__tests__/stats.spec.ts`（6 例）：只按当前 space_id 单次请求、无跨空间
    总计（两空间之和不得出现）、6 态状态面板（running/failed 无数字）、stale 数据
    + 过期标注、刷新强制重读仍只针对当前空间、404 合同未就绪安全态、lineage 口径。
  - `views/__tests__/settings.spec.ts`（9 例）：四分区渲染 + 复用组件挂载、空间
    管理不在设置内、披露矩阵（全局/逐空间/高敏感禁用）、主题切换三态同步、我的
    数据导出/删除注销 + 会话清理。
  - store/API 层：`stores/__tests__/memory.spec.ts`（8 例，含 resetForSpace 清
    RAG 引用）、`stores/__tests__/notifications.spec.ts`（10 例，含 markRead 与
    ActionCard 分离、epoch 丢弃迟到响应）、`stores/__tests__/spaceStats.spec.ts`
    （6 例）、`api/__tests__/notifications.spec.ts`（8 例）、
    `api/__tests__/spaceStats.spec.ts`（5 例）、`useSpaceContext.spec.ts`（17 例，
    含三 store 清理时机断言）。
- 门禁（2026-09-02，frontend/）：`npm run type-check` 零错误；`npm run lint`
  零告警；`npm test` 56 文件 427 用例全绿；`npm run build` 成功（仅存量 index
  chunk >500kB 警告）；`git diff --check` 干净；Phase 5 新改文件 grep 无 v-html、
  无硬编码色值、无 axios 直连、无 `/users`/旧 graph 引用。
- 375px paper/modern 人工走查仍按计划留待 Phase 7 一并执行并记录。

## Phase 6（2026-09-02）实现记录：空间管理和系统管理员边界

### SpaceManagementView 五分区侧栏（design.md §5.5）

- 结构重排为五分区：**概览 / 成员 / 邀请与申请 / Bridge 通知 / 空间设置**。
  桌面端为页面内 sticky 侧栏（`management-nav`，按钮 + aria-pressed），≤768px
  时同一 nav 以 CSS 变为顶部水平可滑动标签条（无新增颜色，全 token 派生）；
  分区切换是纯页面 UI state，不写 localStorage。
- **概览**：沿用原 space-overview 卡（空间名称/类型/当前角色管理员标记/
  成员·待处理计数），数据全部来自 spaces store 的服务端投影。
- **成员**：复用 `SpaceGovernancePanel`（成员表 + 邀请搜索 + 管理员交接），
  并为其扩展一列**管理员专属操作**（`canManageSpace` 门控）：active 非本人成员
  「移除」、pending 成员「撤回」，均走既有 `spaces.leaveOrRemove`（DELETE
  /space-memberships，store 内部 `load()` 重读服务端，无乐观行删除）；确认交互用
  NPopconfirm（trigger=click）。待确档引用（PendingProfileRefs）归入成员分区。
- **邀请与申请**：复用既有 `InviteMemberDialog`（「邀请成员」按钮，canInvite 门控）、
  `SpaceManagerApplicationPanel`（申请接手 + 我的申请状态 + 原管理员交接工单）与
  `ActionCardInbox`（待办入口），全部走既有流程组件，不新增任何直接编辑路径。
- **Bridge 通知（只读）**：只渲染 notifications store 中当前空间 `kind='bridge'`
  的通知（NoticeItemRow 纯展示：安全摘要 MaskedField、领域状态标签、通知时间），
  **无 approve/reject/consent/revoke/已读等任何操作控件**——分区零按钮、零链接，
  错误态为纯文本；端点 404（BLOCKER 占位）→ 与通知页同一口径的「合同未就绪」安全态；
  无缓存时补拉一次（带 ETag），空间上下文由 hasAccess（currentSpaceId === 目标空间
  且 canManageSpace）保证。
- **空间设置**：既有 `PATCH /spaces/{space_id}` 合同（后端 `SpaceUpdate` 仅 name，
  服务端 `_require_space_manager` 判权），新增 `api/spaces.updateSpace` +
  `spaces.rename` action（成功后用服务端响应替换列表条目，无乐观改名）；仅空间名
  一个字段，无任何授权字段，64 字符上限与后端一致。
- **双保险拒绝态**：路由守卫 fail-closed（已有）+ 页面内 `management-denied`
  （member/guest/家庭主体 platform 标记均渲染拒绝态，且不渲染分区导航与治理面板）。

### isSystemAdmin 判定审计结论（已落测试）

- `stores/auth.ts` 的 `isSystemAdmin` 唯一判定源是
  `user.principal_type === 'system_admin'`；user 来自服务端签名会话响应
  （/auth/login、/auth/login/select、/auth/refresh 的 TokenPairResponse；系统主体
  按设计不调 GET /me，refresh 响应即权威投影）。`is_admin` 是 v2 兼容显示字段、
  `platform_role` 是家庭端平台运营标记，两者均不参与主体判定——判定实现无需修正，
  语义以注释 + `stores/__tests__/auth.spec.ts` 固定。
- **主体互斥切换的家庭缓存清理（补齐）**：审计发现唯一登录端点 `/auth/login`
  同时服务家庭用户与系统管理员（后端先查 system_admins 再查 users）。原实现只在
  登出/401/clearSession 清家庭 stores；现抽出 `clearFamilyCaches()`（原 clearSession
  的 store 清理体），`login`/`selectCandidate` 换入 system_admin 主体时先清空全部
  家庭敏感 store 再落 token（`ensureNoFamilyCachesForPrincipal`），保证系统管理员
  进入后台壳时家庭 stores 为空。family_user 登录不触发清理（清理点=登出/失效/
  系统主体换入），refresh 不换主体。

### 系统管理员边界落点（PRD §2.7）

- 新增占位路由 `path: '/system-admin/login'`、name `system-admin-login`、
  `meta: { public: true, chrome: 'blank' }` + 最小占位页
  `views/SystemAdminLoginView.vue`：无登录表单、无家庭壳/后台壳、无家庭跳转链接，
  页面明示真实登录流程由 `09-01-system-admin-governance-routes` 任务实现（该任务
  交付时替换此占位页即可，路由 name 保持）。决策理由：后端登录端点已可签发
  system_admin token，守卫互斥必须先于对方任务落地，占位页仅固定边界不做业务。
- `App.vue`：占位页除 blank chrome 外还排除 AssistantLauncher 家庭悬浮入口。
- 守卫补齐：已登录主体访问 system-admin-login → 系统管理员回 `system-admin` 后台、
  家庭用户回 `family-space`；家庭用户互斥拦截名单加入 system-admin-login；未登录
  可达占位页（public，独立于家庭登录页）。既有互斥（system_admin → 家庭页弹回后台、
  family_user → 后台路由弹回家族树）不变。
- 家庭壳无后台入口：AppShell 本就不渲染系统后台链接（Phase 2 事实），本阶段以
  组件测试 + 源码级断言（`shellSource` 不含 `system-admin`）固定为红线。

### 测试与验证（Phase 6）

- `views/__tests__/space-management.spec.ts` 重写（8 例）：五分区侧栏与逐分区切换、
  概览数据源、移除成员走既有命令 + 服务端 reload、邀请弹窗/申请面板/ActionCard
  入口挂载、Bridge 分区仅 bridge 通知且零操作控件（按钮/链接/操作 data-test 与
  通知·卡片变更 API 零调用双重断言）、404 合同未就绪安全态、空间设置仅一个输入 +
  保存走 updateSpace + 服务端响应回写、非管理员（member/guest/is_admin 家庭主体）
  拒绝态无导航与治理面板。
- `router/__tests__/guard.spec.ts` 新增 3 例：家庭用户直达 `/system-admin/login`
  弹回家族入口、system_admin 访问占位弹回后台、未登录可达占位页。（普通成员/
  platform_operator 直达空间管理被拒、system_admin/family_user 既有互斥用例已在。）
- `stores/__tests__/auth.spec.ts` 新增（5 例）：principal_type 语义固定、
  system_admin 登录/selectCandidate 清空家庭 stores、family_user 登录不误清、
  登出回归。
- `AppShell.spec.ts` 新增 1 例：家庭壳即使对空间管理员也不渲染系统后台入口
  （链接/文案/源码级三重断言）。
- 门禁（2026-09-02，frontend/）：`npm run type-check` 零错误；`npm run lint`
  零告警；`npm test` 57 文件 441 用例全绿；`npm run build` 成功（仅存量 index
  chunk >500kB 警告）；`git diff --check` 干净；Phase 6 新改文件 grep 无 v-html、
  无硬编码色值、无 axios 直连、无 element-plus 残留。
- 375px paper/modern 人工走查仍按计划留待 Phase 7 一并执行并记录。

## Phase 7（2026-09-02）响应式与视觉质量记录

### 移动端壳（≤768px）实现要点（AppShell.vue）

- 顶部两行布局：第一行品牌 + 动作（通知铃铛 / Assistant 占位 / 主题切换 / 账号菜单），
  第二行空间选择器整行置底（`order: 10; flex: 1 1 100%; max-width: none` +
  `.shell-topbar { flex-wrap: wrap; row-gap: 6px }`）——375px 竖屏/横屏切换时
  选择器始终有可用宽度，不与品牌/动作按钮争抢空间，不错位（flex-wrap + min-width: 0）。
- 底部一级导航只保留家庭/家族树/记忆/统计四个入口（52px ≥ 44px 点按目标）；
  设置/通知/空间管理不进底部导航：通知在顶部铃铛入口，设置/空间管理在账号菜单
  （AppShell.spec 断言四入口文本数组 + 底部无 设置/通知/管理）。
- 主内容 `padding-bottom: calc(64px + env(safe-area-inset-bottom, 0px))`、底部导航
  `padding-bottom: env(safe-area-inset-bottom, 0px)`：不遮挡内容且适配 iOS 安全区。
- 桌面侧栏导航/顶栏按钮/账号菜单项均为 44px 点按目标（既有），未改动。

### 每页 375px 布局实现要点

- **HouseholdCardView**：≤768px 双栏 `grid-template-columns: 1fr` 单列堆叠，
  DOM 顺序即堆叠顺序（本人资料 self-profile 在上、成员区在下，测试以
  compareDocumentPosition 断言）；成员网格 `auto-fill minmax(200px,1fr)` 在 375px
  自然收敛为单列，网格/列表均 `max-height: 460px; overflow-y: auto` 纵向滚动，
  无横向滚动；退出按钮 `min-height: 44px`，移动端媒体查询把 small 按钮/成员
  视图切换（NRadioButton）补足 44px。
- **FamilyTreeView**：移动端画布触控由 VueFlow 显式契约保证——模板显式传
  `:zoom-on-pinch="true"`、`:pan-on-drag="true"`、`:zoom-on-scroll="true"`
  （双指缩放/拖拽平移），「回到自己」「适应画布」「重新加载」按钮保留；工具栏
  ≤768px 收敛为紧凑单行（`flex-wrap: nowrap; overflow-x: auto`，按钮
  `min-height: 44px`），仅是工具条形态变化——**列表布局未恢复**（family-tree.spec
  断言 `<template>` 段不含「列表」）。画布区 ≤768px 由 global.css 既有
  `.canvas-wrap { min-height: 320px }` 承接。
- **PersonProfileView**：本就是单列纵排（max-width 720），返回按钮 44px、关系行
  44px 已达标；≤480px 隐藏「查看关系说明」次要文案，无横向滚动，未新增改动。
- **MemoryView / MemoryManager**：≤600px 五标签变分段控制器外观（既有），
  本阶段补标签 `min-height: 44px` 点按目标；页面顶栏窄屏隐藏装饰性标签
  （既有），候选/记忆列表单列纵排。
- **NotificationsView**：单列三分区（既有）；新增 ≤600px 头部动作按钮
  （全部标记已读/去处理/重新加载）44px 点按目标。
- **StatsView**：页面单列纵排（既有）；摘要卡 ≤600px 降级两列（既有，375px 可读），
  新增刷新按钮 44px 点按目标。
- **SettingsView**：新增 ≤600px inline 表单（修改名字）纵向堆叠
  （`.n-form--inline .n-form-item { width: 100% }`），375px 不溢出；
  主题预览卡 ≤480px 单列（既有）。
- **SpaceManagementView**：Phase 6 已有 ≤768px 分区导航变顶部水平可滑动标签条
  （`overflow-x: auto`）+ 分区按钮 44px（既有，本阶段仅补测试断言）。
- **表格类内容降级**（页面本身无横向滚动）：DisclosureMatrix / DataRightsPanel /
  SpaceGovernancePanel 三处 NDataTable 增加 `scroll-x`（矩阵按逐空间列数计算
  `194 + 120 * max(1, spaces.length)`；数据权利表 640；成员表 480），表格在
  自身区域内横向滚动，页面不横向滚动。

### 静态星空/点阵背景覆盖确认

- 覆盖面：`tokens.css` body 层点阵（`--fg-dot` / `--fg-dot-gap`，随主题联动）+
  `AppShell.vue` `.shell-main` 双层 radial-gradient 星空/点阵层
  （`color-mix(in srgb, var(--fg-ink) 16%/7%, transparent)`），普通家庭壳全部页面
  （家庭卡/家族树/个人页/记忆/通知/统计/设置/空间管理）都在 `.shell-main` 内。
- 双主题：只用现有 `--fg-*` token 派生（paper 暖点阵 / modern 冷灰点阵均生效），
  无新色值；无 animation/transition/粒子（AppShell.spec 源级断言
  `not.toMatch(/animation|@keyframes|transition/)` 继续通过）。
- 对比度：内容卡片一律 `--fg-surface-raised`（paper #fdfbf6 / modern #ffffff，
  不透明），背景点阵不降低内容对比度；`.fg-badge` 等文本对比维持既有 WCAG 记录。

### 质量门禁静态检查（2026-09-02，frontend/ 下全量 grep 结果原文）

1. **element-plus**（`grep -rEn "El[A-Z]|<el-|v-loading|el-loading|element-plus|--el-|@element-plus" src vite.config.ts index.html tsconfig.json`）：
   仅命中 `src/views/__tests__/responsive-375.spec.ts` 的门禁断言字符串与注释
   （3 行，任务允许测试断言字符串）；生产代码、package.json、package-lock.json
   零命中（`grep -c "element-plus" package.json package-lock.json` → `0 / 0`）。
2. **v-html**（`grep -rn "v-html" src`）：仅命中上述测试文件断言字符串（2 行）；
   生产代码零命中。
3. **views/components 直连 axios/apiClient**（`grep -rEn "from ['\"]axios['\"]|from ['\"]@/api/client['\"]|import axios" src/views src/components`）：
   零命中（exit=1）。
4. **硬编码颜色**（`grep -rEn "#[0-9a-fA-F]{3,8}\b|rgb\(|rgba\(|hsl\(" src --include="*.vue" --include="*.ts" --include="*.css"`，排除 `__tests__` 与 `src/styles/`）：
   **零命中**（exit=1）。允许范围内命中：`src/styles/tokens.ts` 43 处（token 定义
   文件）；`src/styles/tokens.css` / `global.css` / `naive-themes.ts` 均 0。
   本阶段修复 1 处存量：`AttachmentsSection.vue` 预览遮罩
   `rgb(0 0 0 / 72%)` → `color-mix(in srgb, var(--fg-ink) 72%, transparent)`。
5. **44px 点按目标抽查**（grep `min-height: 44px|min-height: 52px|width: 44px`）：
   AppShell 品牌链接/侧栏导航/顶栏按钮(44×44)/账号菜单/底部导航(52px)、
   HouseholdCardView 退出按钮+成员卡+成员行+移动端 small 按钮/视图切换、
   PersonProfileView 返回按钮+关系行、SpaceManagementView 分区按钮、
   MemoryManager 移动端标签、StatsView/NotificationsView 动作按钮——全部 ≥44px。

### 375px 双主题走查

- **375px 真实浏览器双主题走查待主会话执行**（paper/modern 各一遍；检查点：
  浮层可关、无横向滚动、44px 点按目标、reduced-motion OS 级开关复核、底部导航
  不遮挡内容、家族树双指缩放/平移/回到自己）。jsdom 无布局引擎，本阶段以
  源级断点/类名/结构断言（`responsive-375.spec.ts` + AppShell/household-card/
  family-tree spec 扩展）先行固定契约。

### 测试与验证（Phase 7）

- 新增 spec：`views/__tests__/responsive-375.spec.ts`（8 例：记忆分段控制器+
  44px 标签、设置 inline 表单堆叠+主题卡单列、统计摘要卡降级+44px、通知 44px、
  空间管理顶部标签条、个人页单列契约、三表 scroll-x、全量源级门禁
  v-html/element-plus/硬编码色值 it.each）。
- 扩展 spec：`AppShell.spec.ts`（+2：底部导航四入口/次级入口走顶部与账号菜单、
  侧栏隐藏+选择器整行+安全区留白+52px 底部链接源级契约）、
  `household-card.spec.ts`（+1：单列堆叠 DOM 顺序+成员区纵向滚动+44px 源级）、
  `family-tree.spec.ts`（+1：触控契约 zoomOnPinch/panOnDrag/zoomOnScroll +
  紧凑工具栏源级 + 模板无列表；VueFlow mock 补三 prop 声明）。
- 门禁（2026-09-02，frontend/）：`npm run type-check` 零错误；`npm run lint`
  零告警；`npm test` 58 文件 463 用例全绿；`npm run build` 成功（仅存量 index
  chunk >500kB 警告）；`git diff --check` 干净。

## 375px 双主题真实浏览器走查记录（2026-09-02，主会话执行）

### 走查环境（隔离，未触碰开发库/用户数据）

- 隔离后端：`DATA_DIR=/tmp/fg-walkthrough`（独立 SQLite，alembic 全链迁移）+ `PERSONAL_FAMILY_VIEW_ENABLED=1 MEMORY_ENABLED=1 RAG_ENABLED=1`，uvicorn 绑 `127.0.0.1:8000`；
- 种子数据（照抄 tests/conftest 造数形状）：本人林晓梅（claimed/identity_confirmed）+ 林建国/王秀英/林守业，household「晓梅的家」（3 成员、本人 space_admin）+ lineage「林氏家族」（3 条 biological_parent 确认事实），`rebuild_view` 后 PFV status=current（4 节点 3 边）；
- 前端：vite dev，代理临时指向 `127.0.0.1:8000`（**已还原**）。注意：本机 `localhost:8000` 被 OrbStack 端口转发（用户容器化开发后端）占用，vite 的 `localhost` 优先解析 `::1` 会打到用户后端——排障时曾因此误连，走查通过显式 `127.0.0.1` 隔离；
- 浏览器：ZCode IAB，375×812 与 1280×900 两视口，纸墨/清雅双主题。

### 发现并当场修复的两个 bug（修复已进入工作树）

1. **P0：未登录打开任意页面陷入 `/login` 无限整页重载**（后端日志 2320 次 bootstrap/status + 2318 次 spaces 401 循环）。根因：App.vue 在 vue-router 初始导航未解析窗口内 `route.meta` 为空 → `isBlankChrome` 误判 → AppShell 先于登录页挂载 → `onMounted` 的 `ensureDefaultSpace` 发起未认证 `GET /spaces` → 401 → 会话过期处理器 `location.assign('/login')` 整页重载 → 同一竞态循环。修复：AppShell 改为 `watch(auth.isLoggedIn)` 触发默认空间选择（未登录不发起），会话过期跳转在已在 `/login` 时不再重复 assign（`stores/auth.ts` + `components/shell/AppShell.vue`）。该 bug 由 Phase 2 引入，此前验证均在已登录态故未暴露。
2. **P1：公示页覆盖当前空间上下文**。会话内从 lineage 家族树点击他人节点进入 `/people/:id` 时，`ensureProfile` 无条件调用 `ensureDefaultSpace`，把上下文改回「最近 household」→ 目标被误判为不可见。修复：已有当前空间时直接沿用（非 lineage → 安全不可见），仅硬刷新直达（无上下文）时兜底做一次默认选择（`views/PersonProfileView.vue`）。

### 走查结果矩阵（全部通过，除注明外）

| 页面 | 375px 纸墨 | 375px 清雅 | 桌面 1280 | 关键行为 |
|---|---|---|---|---|
| 登录页 | ✅ | ✅ | — | 名字+PIN 表单、无横向滚动 |
| 家庭卡 `/` | ✅ | ✅ | ✅ | 合同未就绪安全态、退出按钮、空间选择器、底部导航 |
| 家族树 `/family-tree` | ✅ | ✅ | ✅ | 三代树状布局、本人高亮「我」、称谓边标签、masked「已隐藏」、summary「族谱摘要·不可展开」、图例/缩放/定位工具齐全 |
| 公示页 `/people/:id` | ✅ | — | — | 会话内就绪：masked 公示字段、档案管理/状态、关系上下文；硬刷新直达 → 安全不可见（无 ID/空间名泄漏） |
| 关系说明 | ✅ | — | — | 主路径、替代路径（暂无）、事实状态、投影版本+时间、申请更正/查看待办 |
| 通知 `/notifications` | ✅ | — | — | 当前空间标签、已读语义文案、BLOCKER 端点安静降级、全部已读禁用 |
| 统计 `/stats` | ⚠️ | — | — | 降级为通用错误态（旧 `/stats` 无空间合同返回 200 + 解码失败），仍安全；服务端合同落地后自然消失 |
| 记忆 `/memory` | ✅ | — | — | 五标签分段控制器、默认私有、无页面级横向溢出（标签行内部滚动符合预期） |
| 设置 `/settings` | ✅ | — | — | 个人资料 + 隐私公示矩阵（DisclosureMatrix）单列可用 |
| 空间管理 | — | — | ✅ | 五分区（概览/成员/邀请与申请/Bridge 通知/空间设置）、概览服务端投影、管理员入口 |
| 壳 | ✅ | ✅ | ✅ | 桌面左侧栏（主/次导航 + 空间管理按权限出现）、移动底部四入口 + 顶部选择器；账号菜单（登出）验证通过 |

交互链路实测：登录 → 默认进入 household 家庭卡 ✅；家庭卡「退出到家族树」→ lineage 提示卡「进入林氏家族」✅；节点点击（自己→家庭卡 / 他人→公示页）✅；返回家族树保持 lineage 上下文 ✅；登出 → `/login` ✅；通知/统计/记忆在 BLOCKER 端点下全部安静降级无假数据 ✅。

### 遗留观察（不阻塞）

- IAB 截图通道在 Vue Flow 画布 fitView 动画期间偶发超时（动画结束后可截图；页面本身无持续动画）——工具限制，非应用缺陷；
- 统计页降级文案为通用错误态而非「合同未就绪」（旧合同 200 导致），服务端空间化统计落地后消除；
- 移动端悬浮 Assistant 按钮与底部导航间距偏小（Agent Runtime 任务实现面板时可一并调整）。
