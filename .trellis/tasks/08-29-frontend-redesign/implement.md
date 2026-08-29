# 实施计划：前端重设计（Naive UI 迁移 + 双主题设计系统）

> 执行顺序即依赖顺序；每个 Phase 结束跑一次门禁并独立 commit（回滚点）。
> 验证命令（除非注明均在 `frontend/` 下执行）：
> `npm run lint && npm run type-check && npm run test && npm run build`

## Phase 0：前置检查 + 基建（token / 主题 / providers / 壳骨架）

- [x] P0-1 前置检查：`npm install naive-ui` 成功（离线风险闸门）✅ 2026-08-29
- [x] P0-2 `styles/tokens.ts`：ThemeTokens 类型 + paperTokens / modernTokens（L1+L2 全量）
- [x] P0-3 `styles/naive-themes.ts`：由 tokens.ts 派生两组 themeOverrides（同文件内显式映射，禁止另写颜色）
- [x] P0-4 `stores/ui.ts` + `stores/ui.spec.ts`：主题状态、localStorage 持久化、`data-theme` 切换（spec 实落于 `stores/__tests__/ui.spec.ts`，与项目惯例一致）
- [x] P0-5 `styles/tokens.css` 静态基座 + 改写 `global.css` body 背景（点阵改 `var(--fg-dot)`，删除写死色值）
- [x] P0-6 `main.ts`/`App.vue`：新增 `n-config-provider`（message/dialog/notification providers）+ token 注入 watchEffect。**注意：过渡期保留 `app.use(ElementPlus)` 与其 CSS import（未迁移页面仍依赖全局注册），P5-9 全库清零时才移除**
- [x] P0-7 `components/shell/AppShell.vue` 骨架 + router `meta.chrome: 'blank'` 标记沉浸页（login/onboarding/change-pin/identity-setup）；App.vue 按当前路由 meta 条件渲染 `<AppShell>`（内含 RouterView + 顶部导航）或裸 RouterView
- [x] P0-8 门禁四绿 + 记录构建体积对比（主 chunk 1159→1340 kB，过渡期双库共存 +180 kB，P5 移除 element-plus 后预期净降；trellis-check 复核通过，0 阻断）
- [x] **Commit `feat(frontend): design tokens + naive-ui 基建`（回滚点 R0）** → aca08ba

> 注：P0 结束时 element-plus 仍在 package.json 且全局注册保留（业务组件尚未迁完，双库过渡共存）；全库移除放 P5-9。

## Phase 1：认证与流程页

- [x] P1-1 LoginView（含 409 challenge 流程视觉）、OnboardingView（一次性 PIN"凭证卡"：大号等宽数字 + 复制 + 票据式警示框，纸墨附印章质感）
- [x] P1-2 ChangePinView（强制改 PIN 白名单逻辑不动）、IdentitySetupView（确档清单 --fg-status-* 徽章：proposed 空心 / confirmed 实底 / disputed 虚线警示）
- [x] P1-3 同步改写 `views/__tests__/{login,identity-setup,settings}.spec.ts` 中库内 DOM 断言（login/identity-setup 包 NMessageProvider 壳、input 断言走 `[data-test] input`、challenge 候选走原生 radio click）
- [x] P1-4 门禁四绿 ✅ 2026-08-29（lint / type-check / test 177 例 / build 主 chunk 1340.87 kB 与 P0 持平）；主会话浏览器实测：纸墨/清雅双主题登录页截图验证通过；trellis-check 条件项（对比度 AA、回车提交回归）已修复并复核
- [x] **Commit（回滚点 R1）** → `771001d`（Phase 1 主体，与并行会话 store 改动合并入库）+ `cd60600`（回车回归测试）。注：本仓库存在并行会话（v2-agent 收口）同时提交，后续提交只 add 本任务文件并提前核对 status

## Phase 2：Home + 成员域

- [x] P2-1 HomeView 布局重构（空间切换器 + 空间类型标识 + 成员卡重绘）
- [x] P2-2 member 域组件：ProfileDrawer、MemberCreateWizard、AddRelationDialog、AttachmentsSection、DataRightsPanel、DisclosureMatrix、SpaceGovernanceDialog、OneTimePinDialog、PendingProfileRefs
- [x] P2-3 MaskedField 主题化锁样式（保持单点实现不分散）
- [x] P2-4 同步改写 `components/member/__tests__/*`、`views/__tests__/home.spec.ts`（+settings.spec 失效断言；新增空间类型标识与邀请行用例；vitest.setup.ts stub jsdom 缺失 API）
- [x] P2-5 门禁四绿（179 例）+ trellis-check 复核（修复：ProfileDrawer 附件区 import 缺失、DataRightsPanel NDatePicker 同类空串 bug 统一映射）
- [x] **Commit `feat(frontend): home/成员域迁移 + 卡片重设计`（回滚点 R2）** → 4b2fe19

## Phase 3：画布域（家族空间/家族树）

- [x] P3-1 FamilySpaceView：移除 Vue Flow `<Background />`（防双层点阵）、Controls `:deep` token 重样式、画布容器 token 化 ✅ 2026-08-29
- [x] P3-2 MemberNode 重绘（姓字衬线纸牌头像/称谓 chip/右下角压角确档章 claimed=实底、其余空心虚线"待确档"）；样式全走 `var(--fg-*)`，零业务请求；视图层 shallowRef + nodeDataMemo 防全画布重渲染
- [x] P3-3 `computeGenerationLanes` 世代泳道底纹（tree 模式 zIndex -1 底纹节点 + "第 N 代"标签）；连线语义 class（confirmed 实线墨色 / proposed 虚线 / disputed 朱砂虚线），fact_state 只读 kinship.cachedResolve 缓存
- [x] P3-4 KinshipTermPanel 四级来源徽章（personal 实底主色/space/locale/system·structural）+ 路径证据 + 替代称谓；RelationLookup 来源标签 + 图上依据 + 词素 chips
- [x] P3-5 空画布居中引导卡（data-test=empty-add-member）；三布局切换单测保持（useLayout.spec 9 例）
- [x] P3-6 useLayout.spec 增泳道 3 例；RelationLookup.spec/ProfileDrawer.kinship.spec 断言迁移；新增 MemberNode.spec 7 例（stub Handle 隔离 VueFlow 上下文）；kinship store spec 不涉及（store 层零改动）
- [x] P3-7 门禁四绿（lint 0 错 / type-check 0 错 / test 189 例 / build 主 chunk 1342.81 kB vs R2 基线 1340.87 kB +1.9 kB）；浏览器走查（三布局双主题 375px）合并至 P5-3 人工验收——compose 栈凭据用户持有（与归档 v2-agent E3 同一约束）
- [x] **Commit `feat(frontend): 画布域重设计（节点/泳道/连线语义）`（回滚点 R3）** → 见 git log
- trellis-check 复核：通过（0 阻断 0 应修）。P2 备忘移交后续 Phase：① FamilySpaceView `void router` 死语句（HEAD 既有）P5 清理；② `listOrdered` 未应用 computeListOrder（HEAD 既有，函数现为已测死代码）后续接线；③ 空间切换 NSelect aria-label 落包裹层的 naive-ui 透传限制 → P6 写入 component-guidelines `input-props` 约定；④ FamilySpaceView 组件级挂载测试缺口（HEAD 既有）→ P6 补测计划
- 移交自归档 v2-agent 任务的 UX 观察：① 每次打开助手面板默认新会话；② reduced-motion 需 OS 级开关人工复核（P4/P5 处理）

## Phase 4：Agent / ActionCard / Memory 域

- [ ] P4-1 agent 域：AssistantLauncher、AssistantPanel、PanelContent、MessageList、AgentComposer、SessionList、ScopeBanner、ErrorNotice、WebCitationList（SSE 流式渲染回归）
- [ ] P4-2 actioncard 域：ActionCardInbox、ActionCardItem（状态徽章阶 + 两步确认 + 双入口同 store 合同保持）
- [ ] P4-3 memory 域：MemoryManager、CitationList（V2.5 合同：候选确认信息完整、跨空间清理、Policy Guard 错误不静默）
- [ ] P4-4 GlobalSearch（下拉/teleport/z-index 回归）
- [ ] P4-5 同步改写 `components/{agent,actioncard,memory}/__tests__/*`、`stores/__tests__/{agent,actionCards,memory,governance,spaces}.spec.ts`
- [ ] P4-6 门禁四绿 + 冒烟
- [ ] **Commit `feat(frontend): agent/actioncard/memory 域迁移`（回滚点 R4）**

## Phase 5：Stats / Admin / Settings + 收尾

- [ ] P5-1 StatsView、AdminView（表格类迁移：el-table → n-data-table）、SettingsView（主题切换入口落位）
- [ ] P5-2 全库 `grep -r "el-" frontend/src` 清零；package.json 移除 element-plus、@element-plus/icons（如有）
- [ ] P5-3 `npm run build` 体积对比记录；375px 视口人工过主要页面并记录到本文件附录
- [ ] P5-4 对比度核验记录（两主题主色/正文 ≥AA）
- [ ] P5-5 门禁四绿
- [ ] **Commit `feat(frontend): 全站迁移收尾`（回滚点 R5）**

## Phase 6（任务收尾，Phase 3 之后任一点可做）

- [ ] P6-1 更新 `.trellis/spec/frontend/component-guidelines.md`：Naive UI 约定、token 红线（组件禁写死色值）、主题扩展规则
- [ ] P6-2 `.trellis/spec/frontend/quality-guidelines.md` 增补主题/迁移相关门禁（如需要）
- [ ] **Commit `docs(spec): 前端规范更新至 naive-ui + 双主题`**

## 风险文件与注意点

- `main.ts` / `App.vue`：全局 provider 变更影响所有页面（P0 集中处理，小步提交）
- `FamilySpaceView.vue`：全项目最复杂视图（约 400 行）；P3 先只动样式层，逻辑不动
- 测试改写禁止 skip：某断言实在无法等价迁移时，改为语义等价断言并在 PR 描述说明
- `ElMessage` 调用点（grep `ElMessage`）需全部换成 `useMessage()`——注意在 setup 上下文外不可用，必要时经 store 转发

## task.py start 前检查

- [ ] implement.jsonl / check.jsonl 已填真实条目（非 _example）
- [ ] 用户已明确批准最终规划汇总
