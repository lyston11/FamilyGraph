# PersonalFamilyView 前端实施计划

## 执行前置

1. 确认父任务 `09-01-personal-family-view` 的 PersonalFamilyView API/store 当前代码和未提交 WIP 归属；
2. 冻结 household card、scoped stats、notifications/read-state 和 system-admin auth 的后端合同；缺合同的页面先做 fixture/guard，不用旧宽接口猜测；
3. 读取 `.trellis/spec/frontend/*`、相关 backend visibility/memory/steward 规范；
4. 检查当前工作树，只修改本子任务范围，保留 Agent Runtime、identity dedupe、system-admin 和其他任务的 WIP；
5. 启动前通过 `task.py validate 09-01-personal-family-view-frontend`，并由用户批准后执行 `task.py start`。

## Phase 1：合同和状态基础

- [ ] 补齐 `types/api.ts` 的 PersonalFamilyView、household card、scoped stats、notification、visibility/masked 状态联合类型；
- [ ] 在 API 层集中实现 runtime guard/normalizer，禁止组件对 `unknown` 做局部 cast；
- [ ] 扩展 `api/personalFamilyView.ts` 与 store：按 `space_id` 读取、ETag/304、request epoch、状态机和安全快照；
- [ ] 新增 household card API/store，若后端合同尚未落地则先以明确 fixture 标记 blocker，不回退 `/users` 或旧 members 列表；
- [ ] 新增/扩展 stats 和 notifications API/store 的空间过滤、read state、领域状态与错误合同；
- [ ] 为每个新 decoder/store action 添加窄测试。

回滚点：只保留类型、guard 和 store 测试，不改页面路由；删除无服务端合同的临时 fixture 不影响现有页面。

## Phase 2：应用壳和空间上下文

- [ ] 重构 `AppShell.vue` 为桌面左侧导航 + 顶部通知/Assistant/账号菜单；
- [ ] 在空间选择器中按 household/lineage 分组，显示类型和当前空间管理员状态；
- [ ] 在 `spaces` store 或小型上下文协调器中实现默认空间选择：最近 household > own/managed household > 第一个 household > lineage；
- [ ] 实现空间切换 epoch 和统一敏感缓存清理：PersonalFamilyView、household、memory/RAG、notifications、ActionCard、关系详情、Assistant context；
- [ ] 更新家庭路由守卫、登录重定向和旧 `/home` 显式重定向；
- [ ] 增加 system-admin/family-user 路由互斥守卫接口，不在家庭壳内渲染系统后台入口；
- [ ] 添加壳、空间切换、401 和默认入口测试。

回滚点：可以只回退路由入口和 AppShell，保留已有 API/store 类型改动。

## Phase 3：家庭卡与家族树替换

- [ ] 将 `HomeView.vue` 的首页语义改为 `HouseholdCardView`，提取旧成员创建/关系/管理入口为可复用流程组件，避免删除领域操作；
- [ ] 实现本人资料区、confirmed household member 网格/列表、家庭状态区、空 household 创建/邀请入口；
- [ ] 保留编辑资料/隐私设置跳转，成员点击进入公示页；
- [ ] 将 `FamilySpaceView.vue` 改造成 `FamilyTreeView` 或拆出等价页面，彻底切换到 PersonalFamilyView store；
- [ ] 移除列表布局入口，保留树状默认和自由画布、缩放/适应/定位自己/刷新/图例；
- [ ] 将 `MemberNode`/edge interaction 改为纯展示事件：自己、他人、关系边分别交给页面处理；
- [ ] 固定静态星空/点阵背景，不加入动画；
- [ ] 增加家庭卡、家族树、旧 graph 不回退、节点/边点击和 status UI 测试。

回滚点：关闭新页面路由并保留 PersonalFamilyView store；不得把旧 graph 重新标记为新树的 fallback。

## Phase 4：个人页、关系说明和 Bridge 待办入口

- [ ] 新增 `/people/:userId` 对应的只读公示页，目标只从当前授权 snapshot 查询；
- [ ] 加入安全的不可见/不存在状态，禁止展示目标 ID、空间名、路径长度和隐藏占位；
- [ ] 实现关系边只读说明面板：称谓、主路径、最多 3 条替代路径、安全来源、状态、更新时间；
- [ ] 增加“申请更正/查看待办”安全跳转，不写 SourceFact；
- [ ] Bridge pending 只在通知/待办处理，active 后 reload PersonalFamilyView；空间管理员只看通知状态；
- [ ] 测试直达/刷新、返回家族树上下文、summary 不展开、Bridge 管理员无操作权。

## Phase 5：记忆、通知、统计和设置

- [ ] 将 `MemoryManager` 整理为五个标签/移动分段控制器：候选、private、household、lineage、引用；
- [ ] 提取独立 memory editor/candidate confirmation UI，确保 private 默认、共享需确认、引用只读、操作后 reload；
- [ ] 新增 `NotificationsView` 和顶部未读入口，区分 read、ActionCard revision 和领域最终状态；
- [ ] 新增/接入 `StatsView`，只显示当前空间授权聚合和更新时间；
- [ ] 重排 `SettingsView` 为资料、披露、账号安全、显示无障碍，复用现有 ChangePin/Disclosure/DataRights；
- [ ] 测试空间切换清理、候选确认 reload、scope 标签、通知已读不执行、统计无跨空间。

## Phase 6：空间管理和系统管理员边界

- [ ] 新增/整理 `SpaceManagementView` 侧栏：概览、成员、邀请与申请、Bridge 通知、空间设置；
- [ ] 入口只由当前 `space_admin` 权限和当前 `space_id` 决定；服务端拒绝手动越权路由；
- [ ] 管理流程复用现有 invite/application/ActionCard，不直接编辑 SourceFact、SpaceMember 或 PersonalFamilyView；
- [ ] 将 `/system-admin/login` 和独立后台壳作为系统管理员任务的边界依赖接入守卫，不把后台详细页面混入本任务；
- [ ] 测试普通成员、platform_operator、system_admin 和 family_user 的路由/导航隔离。

## Phase 7：响应式与视觉质量

- [ ] 桌面侧栏/顶部壳完成，移动端顶部空间选择器 + 底部一级导航完成；
- [ ] 家庭卡在 375px 上下堆叠，成员两列/单列可滚动；
- [ ] 家族树移动端支持缩放、平移、定位自己，不恢复列表布局；
- [ ] 个人页、记忆、统计、设置、管理页单列布局；
- [ ] 静态星空背景覆盖普通家庭壳主要页面，paper/modern 均使用现有 token；
- [ ] 检查无 Element Plus、无硬编码颜色、无 `v-html`、无组件直连 axios、点击目标 ≥44px、无横向滚动；
- [ ] 在 375px paper/modern 下人工走查并记录问题/结果。

## 验证顺序

1. `cd frontend && npm run type-check`
2. `cd frontend && npm run lint`
3. 运行新增/修改的 Vitest 定向测试；
4. `cd frontend && npm test`
5. `cd frontend && npm run build`
6. 检查 `git diff --check`；
7. 若触及 backend contract，运行对应 backend 定向 pytest/mypy/ruff；
8. 运行 `python3 ./.trellis/scripts/get_context.py --mode phase --step 2.2` 并按 `trellis-check` 做跨层检查；
9. 用户批准后运行 `task.py validate`，记录所有证据到 `check.jsonl`。

## 完成定义

- 所有勾选项完成，且没有用旧 graph、全局 users API 或本地关系白名单替代服务端投影；
- 家庭用户和系统管理员路由/认证/缓存边界通过测试；
- 前端四项质量门禁全绿，375px 双主题走查有记录；
- 相关 API 类型、runtime guard、store 和页面行为有对应测试；
- 未把系统管理员后台、推荐 UI、关系事实写入或跨空间授权带入本子任务；
- 完成前运行 `trellis-check`，随后更新规范/任务记录；不在子任务实现阶段提交无关 WIP。
