# PersonalFamilyView 前端技术设计

## 1. 设计原则

### 1.1 两种页面语义

前端把当前空间类型映射为页面语义，但不把它们合并为一个授权上下文：

```text
current household space ──> HouseholdCardView
current lineage space   ──> FamilyTreeView (PersonalFamilyView)
```

`HouseholdCardView` 的成员列表来源于服务端 household 投影；`FamilyTreeView` 的节点和边来源于 `personalFamilyView` store。任何一方都不能从另一方的数组、关系路径或同空间成员关系推导数据。

### 1.2 服务端是唯一真源

完整数据流为：

```text
backend authorized projection
  → api module (HTTP + runtime decoder)
  → Pinia store (space-keyed server state)
  → page/view model
  → presentational components
```

- views 不直接调用 axios；
- 画布组件不请求数据、不读取路由后重新拼图；
- API 层拥有 `unknown` 到共享 TypeScript 类型的解码；
- store 保存服务端返回的状态和版本，不做乐观写入；
- 页面只负责排版、状态表达和触发领域 API；
- 任何 401/空间切换都清除敏感 store，旧请求即使晚到也不能重新写回当前上下文。

### 1.3 不复制后端授权

前端可以根据已解码的 `visibility_level` 决定显示标签和控件，但不能实现自己的 VisibilityPolicy、关系遍历、家庭归属判断、推荐资格判断或 scope 拼接。`none` 节点不会形成前端占位；`lineage_summary` 只读且不可继续展开。

## 2. 页面与路由设计

保留现有家庭用户认证和 PIN 强制修改流程，重排已认证主壳的路由：

| 路由 | route name | 页面 | 数据来源 |
|---|---|---|---|
| `/` | `home` | 我的家庭 / HouseholdCardView | household view store + spaces/auth |
| `/family-tree` | `family-space` | 家族空间树 / FamilyTreeView | PersonalFamilyView store |
| `/people/:userId` | `person-profile` | 他人只读公示页 | 当前 lineage snapshot 中的授权节点 |
| `/memory` | `memory` | 记忆与知识 | 现有 memory store/API + action cards |
| `/stats` | `stats` | 当前空间统计 | scoped stats API/store |
| `/notifications` | `notifications` | 通知与待办 | notifications store/API + ActionCard 状态 |
| `/settings` | `settings` | 设置 | auth/disclosure/data-rights/ui stores |
| `/spaces/:spaceId/manage` | `space-management` | 当前空间管理 | spaces/members/application APIs |
| `/login` | `login` | 家庭用户登录 | family auth |
| `/change-pin` | `force-change-pin` | 强制改 PIN | family auth |
| `/onboarding` | `onboarding` | 创建/加入空间引导 | existing onboarding APIs |
| `/system-admin/login` | `system-admin-login` | 系统管理员登录（依赖任务） | system-admin auth |

实现时尽量保留现有 route name 的外部引用：`home` 继续作为登录成功默认目标，`family-space` 改为 `/family-tree` 而不是继续指向旧空间图。旧 `/home` 如仍被历史组件使用，只做显式重定向到 `/`，不能继续挂载旧成员混合页。

`/people/:userId` 必须带当前 lineage 的安全上下文（由 store/current space 管理，不接受 viewer/root 路由参数）。直达或刷新时先加载当前 lineage 投影，再验证目标是否出现在快照中；不在快照中的 ID 统一进入安全不可见状态。点击自己的节点不进入该路由，而是切换到 household 卡或 household 空状态。

## 3. 统一家庭用户应用壳

### 3.1 AppShell 结构

在现有 `AppShell.vue` 基础上重构为统一壳：

```text
┌──────────────┬─────────────────────────────────────┐
│ brand        │ notifications · Assistant · account │
│ space picker ├─────────────────────────────────────┤
│ 我的家庭     │                                     │
│ 家族树       │          router-view                 │
│ 记忆与知识   │                                     │
│ 统计         │                                     │
│ 设置         │                                     │
│ 空间管理*    │                                     │
└──────────────┴─────────────────────────────────────┘
```

- 桌面端左侧固定导航；顶部放通知、Assistant、用户菜单；
- `空间管理` 按当前空间的 `space_admin` 权限动态出现；不只检查用户全局角色；
- 当前空间选择器按 household/lineage 分组，显示名称、类型和当前空间管理员标记；
- 移动端顶部显示空间选择器，底部只保留家庭、家族树、记忆、统计四个一级入口，设置/通知/管理通过顶部或更多菜单进入；
- 主内容区域承载静态星空/点阵背景和页面卡片；背景不使用动画，不依赖节点/通知数据，不替代内容对比度；
- Assistant 作为壳级可收起面板注入当前 `space_id` 和页面上下文，但不能读取页面之外的授权数据；登出/401/空间切换清理会话上下文。

### 3.2 空间切换事务边界

由 `spaces` store 或一个仅负责协调的 `useSpaceContext` composable 统一执行：

1. 校验目标空间存在于服务端空间列表；
2. 先更新切换 epoch/context，令旧请求结果失效；
3. 清除旧空间的 PersonalFamilyView、household card、memory/RAG、notifications、ActionCard、relationship detail 和页面 selection；
4. 按空间类型加载新的服务端投影；
5. 重新计算当前空间的管理员入口、首页目标和 Assistant context；
6. 失败时只显示当前空间的安全失败状态，不恢复旧空间敏感数据。

最近使用的空间只作为内存/UI 偏好（必要时使用允许的 UI localStorage），不能保存 viewer/root、人员、记忆、通知或授权结果。

## 4. Store 与 API 边界

### 4.1 PersonalFamilyView

复用现有 `api/personalFamilyView.ts`、`stores/personalFamilyView.ts` 和 `types/api.ts`，但补齐：

- 按明确 `space_id` 的 `load/refresh/getView` API，不依赖含义不清的全局 `current` getter；
- `status/view_version/computed_at/stale_reason/etag` 的完整状态；
- 304/ETag 处理，若未变化保留同一安全快照；
- 请求 epoch 或 AbortController，避免空间切换后的旧响应回写；
- 节点/边/路径的运行时守卫和有限 display/masked 联合解码；
- `none` 节点和不安全字段在 API/store 边界被丢弃，而不是由组件决定是否隐藏；
- `lineage_summary` 标记为不可扩展。

FamilyTreeView 只消费该 store，不再调用 `graph` store 的 `/api/graph/me` 数据作为页面数据源。旧 graph store 可以暂时被仍未迁移的其他流程使用，但不能被 PersonalFamilyView 页面读取或作为 fallback。

### 4.2 Household card

新增 `api/household.ts` 与 `stores/household.ts`，消费服务端 `HouseholdCard` 投影。最低合同：

- `space_id`、`space_kind='household'`、空间名称、view/version 更新时间；
- 当前 viewer 的授权 profile display；
- confirmed active household members 的授权 display、家庭标签和字段级状态；
- 空成员/创建家庭/邀请家人的服务端允许动作提示；
- 不包含 lineage 节点数组、不包含隐藏成员数量、不包含其他空间资料。

该接口必须由服务端按当前认证主体和目标 household 授权，前端不能循环调用 `/users`、`/members` 再自行合并成家庭卡。若服务端尚未提供此投影，先完成 contract fixture/type test，不能用旧 `members` store 冒充完整家庭卡。

### 4.3 公示个人页和关系说明

公示个人页优先从当前 `PersonalFamilyView` 快照中查找目标，以保证入口和家族树使用同一授权快照。若产品合同最终需要独立刷新，则新增一个同等或更窄的 authorized person projection API；禁止按 `userId` 调用宽泛用户详情接口。

在 store 层提供 `getVisiblePerson(spaceId, userId)` 和 `getRelationshipDetail(spaceId, userId)` 之类的已解码查询，组件不读取原始 `unknown` 或 JSON path。关系边详情使用当前边的路径/term/reason/version，保持面板上下文，不产生 SourceFact 写操作。

### 4.4 Memory、通知、统计、管理

- 记忆继续复用现有 memory API/store 和 `MemoryManager`，按标签切换 scope；所有变更后 reload；
- 新增 `api/notifications.ts`、`stores/notifications.ts`，统一读/未读/ActionCard 引用/领域状态合同；通知 `read_at` 与 ActionCard revision 独立；
- 统计 API 增加当前 `space_id` 的服务端授权聚合合同，由 `api/stats.ts`/`stats` store 解码；前端不从 view nodes 计算敏感统计；
- 空间管理复用现有 `spaces`、members、manager application、ActionCard 组件和命令入口，所有操作完成后 reload；
- 共享 API 类型集中在 `types/api.ts`，新增响应不得在组件中局部定义同名结构。

## 5. 页面设计

### 5.1 HouseholdCardView

结构：

```text
[退出 → 家族树]  家庭名称 / 管理员标记
┌──────────────────┬──────────────────────────────┐
│ 本人资料         │ 家庭成员                     │
│ 头像、姓名、状态 │ confirmed household members  │
│ 编辑资料         │ 小卡片网格 ↔ 列表             │
│ 隐私与公示设置   │ 点击 → /people/:id            │
└──────────────────┴──────────────────────────────┘
[家庭状态：成员数、待办入口、进入家族树、创建/邀请空状态]
```

- 仅 `household` 投影的成员进入右栏；不显示普通亲属或 lineage summary；
- 资料编辑和 disclosure 设置是跳转入口；不在卡片内行编辑；
- 家庭为空时保留卡片骨架，显示创建/邀请流程，不能静默创建 household；
- 退出优先回到与当前家庭上下文关联的 lineage；没有时回到安全空状态；
- 桌面端卡片居中/偏左，移动端本人信息在上、成员区域在下。

### 5.2 FamilyTreeView

- 以 PersonalFamilyView `nodes/edges` 构造 Vue Flow data，构造逻辑在页面或专用 view-model，不在 `MemberNode` 发请求；
- 默认树状 layout，可切换自由画布；布局偏好只属 UI state；
- 保留缩放、适应画布、定位自己、刷新、图例；移除列表入口；
- 自己节点强调只表达当前主体，不改变权限；
- 节点点击自己→家庭卡；节点点击他人→公示个人页；关系边点击→只读关系说明面板；
- summary 节点没有展开按钮和家庭卡入口；none 节点不生成 Vue Flow node；
- stale/failed 使用最近安全投影但清楚标注版本/更新时间；never/queued/running 无数据时显示状态面板；
- 旧 graph 的成员/边/位置不能混入 PersonalFamilyView。

### 5.3 PersonProfileView 与 RelationshipDetailPanel

个人页为只读纵向页面：身份头部、公示字段、masked 状态、当前关系上下文、ActionCard 跳转。页面保留“返回家族树”并恢复来源 route 的空间和画布上下文标记；刷新后恢复到同一 lineage 但不保存敏感节点到 URL。

关系面板包含称谓、主路径、最多三条替代路径、安全来源摘要、事实状态、更新时间和“申请更正/查看待办”安全跳转。遮罩目标、其他空间事实、private Memory/Session 和模型自由文本不显示。

### 5.4 MemoryView、StatsView、NotificationsView、SettingsView

- `MemoryView` 使用 5 个标签，候选/正式/引用视觉分离；新增/编辑/确认使用全屏页面或面板；
- `StatsView` 显示当前空间、范围、更新时间、授权摘要卡和关系分布；无跨空间总计；
- `NotificationsView` 分待处理、通知、历史；已读不改变 ActionCard；ActionCard 操作仍跳既有流程；
- `SettingsView` 重排为个人资料、隐私与公示、账号与安全、显示与无障碍；保留现有 ChangePin、DisclosureMatrix、DataRightsPanel；
- 所有页面使用统一状态面板和当前空间标签，错误不退化为普通空状态。

### 5.5 SpaceManagementView

使用当前空间管理侧栏：概览、成员、邀请与申请、Bridge 通知、空间设置。侧栏入口由当前空间 `space_admin` 授权决定。Bridge 通知只显示安全状态和通知时间，不显示 approve/reject/edit/revoke 控件；普通成员直达路由显示安全拒绝。

## 6. 认证和系统管理员边界

家庭用户路由守卫只接受 `principal_type=family_user` 和满足 PIN/identity 状态的认证。系统管理员路由守卫只接受 `principal_type=system_admin`，且所有家庭 stores、Assistant context 和家庭壳状态都必须清空。

系统管理员入口使用 `/system-admin/login`，不在普通家庭用户导航或登录页中提供显眼跳转。系统管理员任务负责其独立 API、页面字段白名单、后台壳和登录流程；本任务只增加守卫互斥测试、家庭壳不渲染后台入口和登出清理。

## 7. 视觉和响应式实现

- 沿用 `--fg-*` token、paper/modern 双主题和现有 Naive UI 组件，不新增 Element Plus 或硬编码颜色；
- 静态星空/点阵背景只使用 CSS 背景层，无 animation/transition 粒子；
- `prefers-reduced-motion` 仍应保持无动态实现，不需要额外动画开关；
- 桌面使用固定侧栏，移动端 375px 使用顶部空间选择器、底部一级导航、单列页面和可滚动成员区；
- 家族树在移动端支持平移/缩放/定位自己；禁止横向页面滚动；
- icon + 文案表达权限状态，不能只靠颜色；主要点击目标至少 44px；
- 家庭卡、个人页和管理页的内容卡片保持足够背景不透明度和 token 对比度。

## 8. 失败与竞态

- 所有 store 请求绑定 `space_id + requestEpoch`；旧请求响应若 epoch 不符直接丢弃；
- 401 由 api/client 统一触发全量敏感 store reset 和登录跳转；
- 403/404 对不可见对象使用同形状安全页面，不显示目标 ID/空间名/数量；
- disabled、never-computed、queued/running、stale、failed 有统一状态组件和可测试 machine state；
- 切换空间时不暂时显示旧空间数据；加载新空间期间显示新上下文的 skeleton/empty，而不是旧卡片；
- 领域操作成功后重新读取服务端状态，失败保留可解释状态，不通过本地 patch 假装成功。

## 9. 测试设计

### Store/API

- PersonalFamilyView runtime guard：字段缺失、未知 status、masked/summary/none、坏 edge、ETag/304；
- household projection guard：空间类型、成员范围、空状态和字段级遮罩；
- space switch：所有敏感 store 清理、旧请求晚到不回写、管理员入口重算；
- notifications：read 与 ActionCard 状态分离、空间过滤、401 清理；
- memory：候选确认后 reload、private/household/lineage scope、撤销引用失效；
- system-admin/family-user route guard 互斥。

### Component/route

- 登录默认选择规则和无 household 空状态；
- FamilyTreeView 不调用 graph API、无列表布局、节点/边点击行为和状态面板；
- summary 不可展开、none 不渲染、masked 可解释；
- household card 只渲染服务端成员、网格/列表切换和移动堆叠；
- person profile 只读、返回上下文、不可见目标安全页面；
- relation detail panel 不写事实；
- app shell、管理员入口、通知入口、Assistant context 和路由守卫；
- 375px 双主题 snapshots/组件走查基础。

### 工程门禁

```bash
cd frontend
npm run type-check
npm run lint
npm test
npm run build
```

实现结束后还要执行 375px paper/modern 人工走查并把结果写入任务记录；若新增 backend contract，则额外按对应 backend 任务的 pytest/mypy/ruff 门禁验证。
