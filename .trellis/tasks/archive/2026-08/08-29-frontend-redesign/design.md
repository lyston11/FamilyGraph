# 技术设计：前端重设计（Naive UI 迁移 + 双主题设计系统）

> 对应 prd.md R1–R8。设计原则：**token 单一来源、库管交互、主题管气质、自绘管门面**。

## 1. 组件库选型：Naive UI（用户已定"换库"，此处为技术选型理由）

| 候选 | 结论 | 理由 |
| --- | --- | --- |
| **Naive UI** ✅ | 采用 | 90+ 组件全覆盖现用面（dialog/drawer/form/select/table/date-picker/switch/steps/descriptions）；TS-first，主题系统类型安全且支持运行时多主题（`n-config-provider` + `themeOverrides`）；ESM tree-shakeable；社区活跃（2026 仍持续更新）。与 Element Plus 组件形态最接近，迁移路径机械可验证 |
| shadcn-vue (reka-ui + Tailwind) | 备选 | 设计自由度最大，但复杂组件（date-picker/table/upload）需自装自维护，430 处 el-* 迁移成本最高；留作未来完全自有设计系统时的演进方向 |
| PrimeVue | 备选 | 功能全、主题强，但组件 API 与现用面差异大，且视觉底子仍偏"库感" |

兼容性：naive-ui（当前 ^2.4x）支持 Vue 3.5；Vite 6 无需特殊插件（组件级按需 import，不用 unplugin resolver，便于控制包面）。

**已知迁移差异（风险点，implement.md 逐项核销）**：

- `ElMessage` 全局函数 → `useMessage()`（需在 App 层包 `n-message-provider` / `n-dialog-provider` / `n-notification-provider`）。
- `v-loading` 指令 → `<n-spin>` 包裹（HomeView 成员列表、画布加载等处）。
- `el-descriptions` / `el-steps` / `el-radio-button` / `el-date-picker` → n 系对应组件，props 命名有差异，逐个核对。
- 弹窗：`el-dialog` → `n-modal`（preset="card"）或 `n-popconfirm` 场景分流；Naive 自带焦点陷阱，a11y 红线可保持。
- z-index / teleport 行为差异：全局搜索下拉、抽屉、悬浮助手需回归。

## 2. 双主题架构：token 单一来源 + 双消费者

### 2.1 三层 token

```
src/styles/
├── tokens.ts        # 唯一权威：ThemeTokens 类型 + paperTokens/modernTokens 常量
├── tokens.css       # 静态基座：字体栈、点阵纹理（用 var()）、reduced-motion、焦点环
└── naive-themes.ts  # 由 tokens.ts 派生的两组 themeOverrides（同文件内显式映射，禁止另写颜色）
```

- **L1 原始 token**：色板（纸白/墨/朱砂/黛青/青蓝…）、字号阶、间距阶、圆角阶、阴影阶。
- **L2 语义 token**：`--fg-surface`、`--fg-surface-raised`、`--fg-ink`、`--fg-ink-secondary`、`--fg-accent`、`--fg-accent-soft`、`--fg-line`、`--fg-status-{confirmed,proposed,disputed,provisional,masked}`、`--fg-dot`（背景点阵色）、`--fg-dot-gap`、`--fg-font-display`、`--fg-radius-card` 等。
- **L3 组件消费**：组件只写 `var(--fg-*)`，禁止写死色值（PRD 验收项）。

### 2.2 主题运行时

- `stores/ui.ts`（新增 Pinia store）：`theme: 'paper' | 'modern'`，初始值读 `localStorage['fg-theme']`，默认 `'paper'`；`setTheme()` 写回并切换 `document.documentElement.dataset.theme`。
- `App.vue`：`watchEffect` 把当前主题的 L2 token 批量 `root.style.setProperty(...)` 注入（保证"token 单一来源"——CSS 与 Naive UI overrides 都由 tokens.ts 生成，无第二份手写色值）；同 watchEffect 选中 `naive-themes.ts` 对应 overrides 传给 `n-config-provider`。
- 主题切换即时生效（CSS 变量 + Naive UI 响应式 props），不刷新页面（PRD 验收项）。
- 背景：`body` 基座在 tokens.css 用 `var(--fg-surface)` + `radial-gradient(var(--fg-dot) …)` 点阵；随主题自动联动，删除现有写死的 global.css 背景。

### 2.3 主题气质规格

| 维度 | 纸墨 paper（默认） | 清雅 modern |
| --- | --- | --- |
| 底色 | 宣纸米白 `#f7f4ed` 系 + 暖调细点阵 | 纯白 `#ffffff` + 冷灰点阵 |
| 墨色 | 深墨 `#2b2b26` 系文字 | 石墨 `#1f2329` 系文字 |
| 主强调 | 朱砂 `#c0392b` 系（主按钮/关键动作） | 青蓝 `#2f6fb3` 系 |
| 次强调 | 黛青/苔绿（状态辅助） | 青/靛（状态辅助） |
| 标题字体 | 宋体栈（Songti SC / Noto Serif SC / serif） | 无衬线栈（现有系统栈） |
| 卡片 | 直角微圆角、纸感边框、无重阴影、悬停微浮起 | 12px 圆角、柔和阴影、白底浮层 |
| 画布 | 摊开的谱卷隐喻：世代横带底纹、墨线连接 | 亮色工作台：浅灰泳道、青蓝连接 |

不引外部 webfont（部署环境离线），全部本地字体栈。

## 3. 布局重构

### 3.1 应用壳（新）

- `components/shell/AppShell.vue`：顶部导航（产品标识、空间切换器、全局搜索、主题切换、用户菜单）；`router.meta.chrome: 'blank'` 时（login/onboarding/change-pin/identity-setup）不套壳、全屏沉浸。
- 各视图删除自拷贝的 topbar，收敛到壳内；HomeView/StatsView 等只保留内容区。
- 悬浮 AssistantLauncher 保持全局（壳外），视觉随主题。

### 3.2 家庭空间画布（FamilySpaceView + MemberNode）

- **世代泳道**：利用 `useLayout` 的 d3-hierarchy 分层输出画横向世代底纹带（`--fg-surface` 分级），节点按层落位——家族树"卷轴"感的主要来源。
- **MemberNode 重绘**：头像位（无头像用姓字衬线字纸牌）、名字 + 称谓（`KinshipTermPanel` 数据源）、右下角状态徽章：`identity_confirmed`=墨点 / `provisional`=空心虚线章。纸墨主题下节点像"名牌/立牌"。
- **连线语义**：confirmed=实线；proposed=虚线；disputed=朱砂虚线（吃 store 里 fact_state，不新增请求，符合"画布禁业务请求"规范）。
- **空画布**：引导动作居中（"添加第一位家人"，规范红线）。
- Vue Flow `Background` 移除（点阵由页面背景 token 提供，避免双层点阵打架），`Controls` 重样式。

### 3.3 成员卡片 / 档案抽屉

- HomeView 成员列表卡：横向卡（头像/姓名/称谓/生卒）+ 右侧状态徽章组 + 快捷动作；空间切换器带空间类型标识（Household=共同生活图标，Lineage=谱系图标）。
- ProfileDrawer：`el-drawer` → `n-drawer`；内部区块"基本信息 / 关系 / 附件 / 数据权利 / 披露矩阵"分节卡化；MaskedField 锁样式按主题定制（纸墨=封泥/印章隐喻的锁形章，保持 `<MaskedField>` 单点实现）。

### 3.4 领域状态视觉语义（R6，全站统一）

| 状态 | 视觉 |
| --- | --- |
| SourceFact confirmed | 实线/墨点/常规 |
| proposed | 虚线/空心徽章 |
| disputed | 朱砂虚线/警示徽章 |
| provisional 人物 | 虚线边节点 + "待确档"章 |
| masked 字段 | 统一锁形章占位（MaskedField） |
| ActionCard pending/viewed/… | 徽章色阶 + 过期灰化（沿用现有 store 状态，不加接口） |
| 称谓来源 | personal/space/locale/system 四级小标签（KinshipTermPanel/RelationLookup） |

## 4. 目录与规范落点

```
frontend/src/
├── styles/{tokens.ts, tokens.css, naive-themes.ts, global.css(精简)}
├── stores/ui.ts                     # 主题状态（新）
├── components/shell/AppShell.vue    # 应用壳（新）
├── components/canvas/MemberNode.vue # 重绘
└── （其余目录不动，逐文件改样式与组件库）
```

迁移期约束：禁止 Element Plus 与 Naive UI 长期共存——按 Phase 整域切换（见 implement.md），每个 Phase 结束时该域文件内无 `el-*` 残留。

## 5. 测试策略

- 断言原则：优先 `data-test` / 文本 / 行为断言；库内类名断言一律改为语义选择器。现有 15 个 spec 逐 Phase 同步改写，禁止整段 skip。
- 新增：`stores/ui.spec.ts`（主题持久化/切换）；MemberNode 状态徽章渲染测试；主题切换后 `data-theme` 与 CSS 变量注入断言。
- 保留红线测试：三布局切换、遮罩渲染（MaskedField）、challenge 登录流程。
- V2.5 合同回归：Memory/ActionCard 双入口同 store、跨空间清理、Policy Guard 错误不静默——现有测试语义保持。

## 6. 兼容与回滚

- npm 离线风险：**Phase 0 前置检查**——`npm install naive-ui` 成功才允许继续；失败则任务退回规划（换库不可行需重新评审，可能降级为"Element Plus 深度定制双主题"）。
- 每个 Phase 独立 commit（见 implement.md 回滚点）；Phase 0（基建+换库落点）最小化：只装库 + token 层 + providers，不碰业务组件，保证其可独立回滚。
- 构建：`vite build` 产物体积变化记录在 Phase 0（Naive UI 按需引入预期 ≤ Element Plus 全量）。

## 7. 性能与 a11y

- 家族视图首渲染 <2s 基线不变；MemberNode 用 `shallowRef`/`memo` 避免主题 watch 引发全画布重渲染（token 注入在 root 层一次完成，不进节点组件）。
- 对比度 AA：朱砂/青蓝主色在两主题底色上均需 ≥4.5:1（正文）/3:1（大字与图形），实现时用工具核验并在 design review 记录。
- 弹窗焦点陷阱（n-modal 自带）、图标按钮 aria-label、表单 label 绑定在迁移时逐个保持。
