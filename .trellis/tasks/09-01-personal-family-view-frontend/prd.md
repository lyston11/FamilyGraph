# PersonalFamilyView 前端产品界面与交互设计

## 1. 目标与边界

本子任务负责把已经实现的 PersonalFamilyView 后端投影接入家庭用户端，并将当前旧 `FamilySpaceView` 的使用体验升级为两层结构：

```text
登录
  └── 当前 household → 我的家庭（家庭空间大卡片）
        └── 退出 → 当前 lineage → 家族空间树（PersonalFamilyView）
              ├── 点击自己 → 我的家庭
              ├── 点击他人 → 只读公示个人页
              └── 点击关系边 → 关系说明面板
```

家庭空间和家族空间是同一家庭用户应用中的不同页面语义：

- `household` 是由创建账户、邀请、申请和接受流程形成的明确家庭归属，前端只展示服务端确认的家庭成员；
- `lineage` 的家族空间树就是当前 viewer 在该空间下的 PersonalFamilyView；
- 普通亲属关系可以出现在家族树，但不能仅凭关系路径进入家庭大卡片；
- PersonalFamilyView、家庭成员资格、推荐资格和空间治理权限不能互相推导；
- 浏览器只能消费服务端授权投影，不自行枚举全局图、拼接隐藏节点或用旧 graph 冒充个人树。

本任务包含普通家庭用户端的应用壳、家庭卡、家族树、只读个人页、关系说明、记忆与知识、统计、通知、设置和当前空间管理入口的前端设计与实现计划。系统管理员是独立主体，使用独立登录接口、路由守卫和后台壳；本任务只固定前端隔离边界，详细后台 UI 由 `09-01-system-admin-governance-routes` 负责。

## 2. 产品决策（已确认）

### 2.1 入口和导航

- 登录后默认进入当前用户自己的家庭空间大卡片。
- 默认空间优先级：最近使用的 household > 用户管理/创建的 household > 第一个可用 household。
- 没有 household 但有 lineage 时进入该 lineage 的 PersonalFamilyView 家族树。
- 完全没有空间时进入现有创建空间引导；不静默创建空间。
- 根路由保持家庭用户默认入口；独立 `/family-tree` 展示家族树。
- 普通家庭用户统一应用壳的一级入口是：我的家庭、家族树、记忆与知识、统计；设置是次级入口。
- 通知与待办通过顶部入口和独立 `/notifications` 页面提供，不占用核心一级导航。
- 当前空间是唯一的页面授权上下文；切换空间不合并多个 household 或 lineage。

### 2.2 家庭空间大卡片

- 每个 household 对应一张独立大卡片，空间选择器负责切换。
- 卡片左侧展示本人资料，右侧展示已经通过领域创建/邀请/申请流程形成的家庭成员。
- 家庭成员不由前端维护关系白名单，也不由 PersonalFamilyView 路径自动推导。
- 普通亲属、家族树远亲、待确认候选和只在 lineage 中可见的人不自动进入家庭卡。
- 成员默认使用小卡片网格，成员较多时支持滚动和列表切换。
- 左上角固定“退出”按钮，返回当前 lineage 家族树；没有对应 lineage 时返回安全空状态或最近可用家族树。
- 本人资料只在卡片上展示，编辑通过“编辑资料”进入现有资料/设置流程；不做复杂行内编辑。
- 家庭卡只提供服务端确认的状态摘要和合法流程入口，不直接修改成员资格或关系事实。

### 2.3 家族空间树

- 家族空间树使用当前 lineage 的 PersonalFamilyView，直接替换旧 `FamilySpaceView` 的 graph 数据源。
- 不保留旧 `/api/graph/me` fallback；PersonalFamilyView 未就绪、stale 或 failed 时显示自身状态，不把旧空间 graph 伪装成个人树。
- 只保留树状布局和自由画布，删除列表布局；默认树状布局。
- 画布工具只保留布局切换、缩放、适应画布、回到自己、重新加载和图例。
- 画布背景使用静态星空/点阵风格，不做闪烁、漂移或粒子动画；背景只负责视觉，不承载数据语义。
- 点击自己的节点进入当前上下文对应的 household 家庭卡；没有 household 时进入“我的家庭”创建/邀请空状态卡。
- 点击他人头像进入独立只读公示个人页；不展示对方家庭成员，不进入对方家庭空间。
- 点击关系边在家族树内打开只读关系说明面板，保持画布位置和缩放上下文。
- `lineage_summary` 使用明确文字/图标标记且不可继续展开；`none` 完全不渲染。

### 2.4 个人公示页和关系说明

- `/people/:userId` 是当前用户从家族树进入的独立只读个人页。
- 页面只消费当前 PersonalFamilyView 快照中已经返回且仍通过查询时授权复核的节点。
- 展示头像、姓名、称谓、已授权公示字段、masked 状态和当前用户可见关系上下文。
- 不返回或通过路由探测不可见目标；找不到当前快照中的目标时显示安全的 404/不可见状态。
- 不提供直接修改对方资料、建立关系、加入空间、查看对方家庭或扩大权限的按钮。
- 已有 ActionCard 时只提供跳转处理入口，实际操作仍走既有确认流程。
- 关系说明面板展示称谓、主路径、最多三条替代路径、安全来源摘要、事实状态和更新时间；不展示私有记忆、隐藏节点、其他空间内部事实或模型自由文本。

### 2.5 记忆与知识

使用标签页（移动端为分段控制器）：

1. 待确认；
2. 我的私有记忆；
3. 当前家庭共享；
4. 当前家族共享；
5. 检索与引用。

有待确认候选时默认进入待确认，否则默认进入私有记忆。候选、正式记忆和检索结果使用不同视觉状态。

- 候选只能确认、拒绝或稍后处理；确认前显示原话、摘要、用途、敏感等级和隐私影响。
- 私有记忆允许本人新增、编辑、撤销和删除，默认 scope 为 `private`。
- 家庭/家族共享必须显示目标 scope、敏感等级和隐私影响并经过明确确认，不提供绕过审计的直接发布。
- 检索结果只读，展示 `citation_handle`、source、scope、revision、trust；保存只能新建候选。
- 所有写入、撤销、删除和确认完成后重新加载服务端状态，不做乐观本地副本。
- 空间切换清除上一空间的共享记忆、RAG 结果和引用。

### 2.6 统计、通知、设置和空间管理

- 统计跟随当前选中空间：household 展示家庭授权聚合，lineage 展示当前 PersonalFamilyView 聚合；不做默认跨空间总计。
- 统计显示授权节点/关系/成员、关系分布、待确认事项、视图状态和更新时间，不泄漏隐藏对象或未授权分支规模。
- 通知中心分为待我处理、通知、已完成/历史；通知已读、ActionCard 处理状态和领域最终状态严格分离。
- 打开通知只变已读，不代表接受申请；全部标记已读不改变 ActionCard 或关系状态。
- 设置分为个人资料、隐私与公示、账号与安全、显示与无障碍；空间管理不放进全局设置。
- 空间管理员入口只在当前具体空间的 `space_admin` 上下文显示，进入当前空间的管理页面；管理范围包括成员、邀请、申请、管理员状态、Bridge 安全通知和空间基本设置。
- 管理员对跨 LineageSpace bridge 只有通知查看权，没有批准、否决、修改或撤销权。

### 2.7 系统管理员隔离

- 系统管理员使用独立 `/system-admin/login` 登录入口、独立认证状态、`principal_type=system_admin` 和后台壳。
- 家庭用户 token 不能进入系统管理员路由，系统管理员 token 不能进入家庭用户壳。
- 家庭用户导航不出现系统管理入口；系统管理员后台不显示家庭卡、家族树、记忆、统计、家庭设置或 Assistant。
- 系统管理员页面和数据字段以 `09-01-system-admin-governance-routes` 为准，本任务只实现/验证路由隔离所需的前端边界。

## 3. 服务端合同依赖

当前仓库已有 PersonalFamilyView API/store，但完整家庭端体验还需要在实现前冻结以下服务端投影合同。前端不得用更宽的旧接口替代这些合同：

1. **HouseholdCard projection**：按 `space_id` 返回当前 viewer 在该 household 中可展示的本人和 confirmed household member 投影、家庭内标签、字段级 visibility 和更新时间；不能通过 `/users` 全局列表自行拼接。
2. **Scoped statistics**：统计接口必须接受当前 `space_id`，并返回服务端授权聚合、范围和更新时间；不能由前端从节点数组推导隐藏对象统计。
3. **Notifications**：按账号和空间返回通知、`read_at`、ActionCard 引用、领域状态和安全 payload，并提供已读更新；Bridge 管理员通知不得带敏感家庭数据或管理操作。
4. **PersonalFamilyView payload decoder**：后端 `display` 的可展示字段与 masked 联合结构必须有明确、稳定的前端运行时解码合同；路由详情页不做本地 `as` 投影。
5. **System-admin auth**：独立登录、刷新、登出和 principal 类型合同供系统管理员任务提供；家庭端只消费守卫结果，不复用家庭用户登录页的隐式分支。

这些是跨层依赖，不授权前端新增本地事实或放宽可见性。若服务端合同尚未落地，前端实现应先停在类型/fixture/contract test，不以旧 API 猜测实现。

## 4. 前端验收标准

- [ ] 登录后按规则进入 household 家庭卡；无 household 时正确进入 lineage 家族树或空状态。
- [ ] 根入口家庭卡、`/family-tree` 家族树、`/people/:userId` 公示页和 `/notifications` 等路由在统一家庭壳内工作，浏览器后退/刷新不丢失安全空间上下文。
- [ ] 家族树只消费 PersonalFamilyView store，不请求或渲染旧 graph；只保留树状/自由画布。
- [ ] 家庭卡只展示服务端 household 投影，不因同空间、亲属路径、候选或前端数组合并自动加入成员。
- [ ] 点击自己、他人头像和关系边的行为分别符合家庭卡、公示页和关系说明合同。
- [ ] 当前空间切换先清理旧空间 PersonalFamilyView、household card、memory/RAG、notifications、ActionCard 和关系上下文，旧请求不能回写。
- [ ] `never_computed/queued/running/current/stale/failed`、disabled、401、403/404 和 masked 均有明确状态；撤权/删除后不显示旧授权节点。
- [ ] `self_private/household_detail/lineage_summary/masked` 使用文字与图标表达，`none` 不留占位或数量信息，summary 不可展开。
- [ ] 公示页和关系说明不提供直接改事实、加成员、扩大权限或查看对方家庭的操作。
- [ ] 记忆页面分区、scope、候选/正式/引用状态和服务端重载行为符合 V2.5 合同。
- [ ] 统计、通知和空间管理均按当前空间/账号权限显示；通知已读不改变 ActionCard/领域状态。
- [ ] space_admin 只看到当前空间管理入口；普通成员手动访问管理路由被守卫和服务端拒绝。
- [ ] system_admin 与 family_user 的路由、token、store 和应用壳完全隔离。
- [ ] paper/modern 双主题使用 token，无新增硬编码颜色或禁用组件库；静态星空背景无动画。
- [ ] 375px 双主题人工走查通过：无横向滚动、主要点击目标至少 44px、家庭成员可纵向浏览、家族树可缩放/平移。
- [ ] 前端 type-check、lint、test、build 全绿；新增组件/store/API/路由均有针对性测试。

## 5. Out of scope

- 不重做后端 PersonalFamilyView 计算、Bridge 授权、SourceFact、Steward 或推荐触发点。
- 不实现推荐 UI；推荐任务只消费 current PersonalFamilyView。
- 不实现系统管理员后台详细页面、治理字段或后台业务流程。
- 不把旧 graph 作为 PersonalFamilyView fallback，也不保留旧 `family/clan` scope 切换语义。
- 不在前端复制 VisibilityPolicy、关系路径计算、成员归属判断或 scope 拼接规则。
- 不实现家庭成员领域流程本身；前端只调用现有创建、邀请、申请和确认命令。

## 6. 依赖与风险

- 依赖父任务已完成的 PersonalFamilyView API/store、桥接 active/revoke 查询时失效和后端授权合同。
- 依赖 `09-01-new-user-family-recommendations` 保持推荐与树归属分离。
- 依赖 `09-01-system-admin-governance-routes` 提供独立系统管理员认证合同；若其尚未实现，先以路由隔离和 mock contract test 固定边界。
- 当前 `HomeView` 携带成员创建、关系、管理员申请等旧混合逻辑，重构时必须抽出可复用流程组件，不能因替换首页而删除既有领域入口。
- 当前 `spaces` store 使用第一个空间初始化且没有完整的 household/lineage 默认选择策略；需要补齐选择 action 和会话内最近空间 UI 偏好，但不得把授权事实持久化到 localStorage。
- 当前 `personalFamilyView` store 的 `current` getter 不能代表多空间上下文；组件必须按明确 `space_id` 读取，必要时扩展 store API。
- 当前统计接口是无空间参数的旧合同，通知中心和 household card 投影尚未形成；这些部分不能用旧接口静默替代。
