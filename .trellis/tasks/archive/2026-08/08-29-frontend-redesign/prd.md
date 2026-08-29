# 前端重设计：组件库迁移与全站视觉改版

## Goal

用户认为当前前端（Element Plus 默认样式 + 平铺布局）"设计风格、框架、布局都太难看"。基于 Obsidian 设计思路文档（`/Users/lyston/Obsidian/lyston/Codex/项目与服务/familygraph/`，重点 00/01/03/04）重做全端视觉与布局：画布（家族图谱）、卡片（成员/行动卡）、家族树、家庭空间等核心界面，建立一套有产品气质、贴合家谱领域的设计语言。

## Key Decisions（用户已拍板）

1. **更换组件库**：不保留 Element Plus（2026-08-29 用户选择）。
2. **双主题设计系统**：纸墨家谱风 + 现代清爽风共存，用户可在 UI 内自由切换（2026-08-29 用户对设计方向问题的回答："1,2 两种风格我都想要，可以自由切换"）。
   - 主题 A「纸墨」：米白宣纸底 + 细点阵、墨色文字、朱砂/黛青点缀、宋体标题、卡片如立牌——默认主题。
   - 主题 B「清雅」：纯白大留白、圆角卡片、柔和阴影、青蓝点缀、全无衬线——次选主题。
   - 组件库选型为技术决策，归 design.md（Naive UI，理由见该文件）。

## Confirmed Facts（代码与文档证据）

### 现状规模

- 前端约 110 个源文件：10 个视图（`src/views/`）、约 40 个组件（`components/{canvas,member,agent,memory,kinship,actioncard,common}/`）。
- Element Plus 使用量（模板标签统计）：el-button ×115、el-tag ×36、el-form-item ×34、el-table-column ×32、el-input ×29、el-radio ×21、el-option ×21、el-dialog ×19、el-alert ×17、el-form ×13、el-radio-group ×12、el-select ×11、el-card ×11、el-switch ×10、el-descriptions-item ×10、el-empty ×8、el-table ×7、el-date-picker ×4、el-drawer ×2、el-tooltip ×2 等约 430 处。
- 图谱画布：`@vue-flow/core` + `@vue-flow/background` + `@vue-flow/controls`，`d3-hierarchy` 布局（`composables/useLayout.ts`）；`FamilySpaceView.vue` 用 `<Background />` 默认点阵。
- 样式现状：`styles/global.css`（字体栈、焦点环、移动端断点、reduced-motion、白底点阵 body 背景）。无设计 token 体系、无主题变量定制。
- 栈：Vue 3.5 + TS 5.7 strict + Vite 6 + Pinia 4 + vue-router 4；Vitest + @vue/test-utils（15 个 spec 文件），部分测试断言 Element Plus DOM（迁移需同步改写）。
- 质量门禁（`spec/frontend/quality-guidelines.md`）：lint / type-check / test / build 四绿；三布局切换、遮罩渲染、challenge 登录流程必须有组件级测试；V2.5 Memory/RAG UI 合同（服务端权威 store、双入口同步、Policy Guard 错误不静默）必须保持。

### 必须遵守的规范约束（.trellis/spec/frontend/）

- `<script setup lang="ts">` 组合式 API；props 类型化；emit kebab-case。
- 遮罩渲染统一走 `<MaskedField>`，禁止散落 v-if 手写。
- 可访问性基线：表单控件绑定 label、弹窗焦点陷阱、图标按钮 aria-label、对比度 AA。
- 空状态必须给引导动作（空画布 → "添加第一位家人"）。
- views 不直接调 axios；画布组件禁止业务请求逻辑（数据由 store 注入）。
- 本任务完成后需更新 `component-guidelines.md`（其中"Element Plus 为主 UI 库"条款将失效）。

### 来自 Obsidian 设计文档的 UI 语义（设计要表达的领域概念）

- 身份三态分离：Account（managed→claimed）、PersonProfile（provisional→identity_confirmed）、SourceFact（proposed→confirmed/disputed）——节点/卡片需可视化状态徽章。
- 空间模型：HouseholdSpace（共同生活）、LineageSpace（谱系）、PersonalFamilyView（个人投影）——家庭空间首页与画布要能表达空间类型差异与桥接边。
- VisibilityPolicy 四级（self_private/household_detail/lineage_summary/none）+ minor/high-risk overlay——MaskedField、披露矩阵要成为一等视觉元素。
- ActionCard 状态机（pending→viewed→accepted→executed/dismissed/expired/superseded）+ 双入口（Chat/Inbox 同 card_id）+ 两步确认。
- 称谓四级来源（personal/space/locale/system）+ 路径证据（主路径/替代路径/fact_state）——KinshipTermPanel/RelationLookup 要展示来源与依据。
- Assistant/Steward 双 Agent 人格；SSE 流式；受控联网引用（WebCitationList）。
- 面向家庭用户，文档强调"老人听得懂"——大字号、高对比、清晰称谓是产品要求而非风格偏好。

## Requirements

- R1 全站更换组件库（Element Plus → Naive UI），完成全部视图/组件迁移，功能行为不变。
- R2 建立设计 token 体系（颜色/字体/间距/圆角/阴影/动效），全站统一消费；token 为单一来源，同时驱动自绘样式与 Naive UI themeOverrides。
- R3 双主题：纸墨（默认）与清雅两套完整主题，UI 内可切换并持久化；背景点阵、字体、配色随主题联动（现有白底点阵背景纳入 token 体系）。
- R4 重新设计核心界面布局：应用壳（导航）、家庭空间画布（图谱）、成员卡片/档案抽屉、家族树布局视图。
- R5 重新设计次级界面：登录/引导/确档、Home、Stats、Memory、Settings、Admin、全局搜索、Agent 面板、ActionCard 收件箱。
- R6 设计语言须表达领域状态（确档状态、事实状态、可见性遮罩、卡片状态、称谓来源），而非纯装饰。
- R7 保持既有测试语义通过（按新库 DOM 更新断言），不降低 a11y 基线，不违反 V2.5 Memory/RAG UI 合同。
- R8 移动端断点（≤768px）布局不劣化。

## Acceptance Criteria

- [x] 全仓库无 `el-*` 组件引用；package.json 移除 element-plus，引入 naive-ui。（R5：目标模式 grep 零命中，lockfile 0 残留）
- [x] 主题系统：默认纸墨主题；设置/导航处可切换到清雅主题，刷新后持久；两主题下背景点阵、字体、主色、组件观感均完整；主题切换不刷新页面。（P0 store + P5 SettingsView 主题卡 + settings.spec 持久化用例；人工复看并入最终走查）
- [x] 设计 token 单一来源；组件不写死颜色值；Naive UI themeOverrides 与自绘样式同源。（P0 架构 + P5-4 对比度核验时同源脚本验证；trellis-check 各 Phase 逐域复核）
- [x] 画布节点、成员卡片、家族树布局按新设计实现并可视化状态徽章（确档状态/事实状态/遮罩）；连线样式区分事实状态。（R3）
- [x] MaskedField/空状态/aria-label/对比度等 a11y 红线在新 UI 下保持；三布局切换、遮罩渲染、challenge 登录的组件级测试保留。（各 Phase check 复核；测试 201 例含全部红线用例）
- [x] `npm run lint`、`npm run type-check`、`npm run test`、`npm run build` 四绿。（R5 终态：201 例，主 chunk 426.50 kB）
- [x] `component-guidelines.md` 更新为新组件库与主题体系约定。（Phase 6，e593ff3）
- [ ] 10 个视图 + 全部组件在新组件库下功能回归：登录→改PIN→确档→空间→画布→成员→关系→行动卡→助手→记忆→统计→管理。（自动化层：201 例组件级测试全绿；**浏览器端到端人工走查待执行**——compose 栈凭据用户持有，清单见 implement.md 附录一）
- [ ] 375px 视口人工过一遍主要页面并记录。（同上，走查清单 implement.md 附录一，17 项 × 双主题）

## Out of Scope

- 后端 API/数据模型/路由结构变更（纯前端改版）。
- 新功能开发（受控联网 UI 等仅保证现有占位不劣化）。
- 国际化体系引入（现有中文文案保留）。
- 暗色模式（双主题均为浅色系；token 架构预留扩展位）。

## Open Questions

（无——设计方向已由用户决策收敛。）
