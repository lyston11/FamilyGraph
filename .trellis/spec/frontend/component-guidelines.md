# 组件规范（v1：naive-ui + 双主题 token 体系，2026-08-29 前端重设计后生效）

## 基础约定

- `<script setup lang="ts">` 组合式 API；禁 Options API。
- 组件命名 PascalCase 多词（MemberCard.vue）；composable 用 useXxx。
- props 用 defineProps<T>() 类型化，必填项显式；emit 事件名 kebab-case。
- 遮罩渲染统一走 `<MaskedField :value :visible>`：visible=false 显示锁样式占位，禁止散落 v-if 手写。
- 可访问性基线：表单控件必须绑定 label；弹窗用 n-modal/n-drawer 焦点陷阱（自管焦点的容器传 `:auto-focus="false"` 并自行实现 Tab 首尾循环）；图标按钮必须有 aria-label；颜色对比度 AA。
- 空状态必须给引导动作（如空画布 → "添加第一位家人"按钮）。
- 画布组件（components/canvas/）禁止业务请求；数据由 store 注入，组件零 api/axios import。

## UI 库：Naive UI（element-plus 已移除，禁止回归）

- 组件按需 import（`NButton`/`NModal` 等），不用全局注册、不用 unplugin resolver。
- 消息/确认/通知一律 `useMessage()` / `useDialog()` / `useNotification()`（App 层 providers 已在 main.ts 备好）；禁止任何全局函数式弹窗。注意只能在 setup 上下文调用，非 setup 场景经 store 转发。
- `v-loading` → `<n-spin>` 包裹；`el-dialog` → `NModal preset="card"`；`el-drawer` → `NDrawer`；`el-table` → `NDataTable`（列定义 `DataTableColumns<T>` + h() render）。
- NSelect 的选项禁用/置灰走 options 数据的 `disabled` 字段（computed 注入），不是模板级属性——迁移 el-option `:disabled` 时最容易丢。
- NBadge 无 `type="primary"`，用 `type="info"`（naive-themes 已把 info 对齐主题色板）。
- 日期选择空值必须 `'' ⇄ null` 映射（NDatePicker 传空串崩溃）。
- 表单控件无法直接绑 label 时（NSelect 等透传限制，aria-label 落在包裹 div），用 `input-props="{ aria-label: '…' }"` 绑到内部 input，并加 data-test。
- 重浮层懒加载模式：壳外悬浮入口（如 AssistantLauncher）按钮保持静态首帧渲染，仅面板内容 `defineAsyncComponent` 拆独立 chunk。

## 设计 token 体系（红线）

三层结构，token 单一来源：

- `styles/tokens.ts`：唯一权威。`ThemeTokens` 类型 + paperTokens/modernTokens 常量（L1 原始色板/字阶/间距阶 + L2 语义 token）。
- `styles/tokens.css`：静态基座（字体栈、点阵、焦点环、`.fg-badge--*` 领域状态徽章工具类），只消费 `var(--fg-*)`。
- `styles/naive-themes.ts`：由 tokens.ts 派生的两组 themeOverrides，同文件内显式映射。

红线：

1. **组件禁止写死色值**（hex/rgb/hsl/named color），只写 `var(--fg-*)` 或既有变量的 `color-mix()` 派生。
2. 新颜色只能进 tokens.ts（L1+L2 双主题同步补齐），再由 naive-themes.ts 显式派生给 naive 组件；禁止在组件或 naive-themes.ts 直接造色。
3. **组件不判断主题**：主题联动靠 CSS 变量自动生效；仅当某主题需要结构性差异（如清雅头像圆角）时才用 `[data-theme='modern']` 局部覆盖选择器，保持克制。
4. 领域状态视觉语义复用既有工具类与 token，不另起炉灶：`.fg-badge--{confirmed,proposed,disputed,provisional,neutral,accent}` + `--fg-status-*`（confirmed 实底/墨点，proposed 空心，disputed 朱砂警示，provisional 虚线"待确档"）。连线语义同阶（实线/虚线/朱砂虚线）。
5. 主题运行时：`stores/ui.ts`（`fg-theme` localStorage + `data-theme`）；App.vue watchEffect 把 L2 token 批量注入 root。新主题值改 tokens.ts 即可全站生效，不要在其他地方再 setProperty。

## 测试约定（naive-ui 下）

- harness：涉及消息/弹层的组件测试包 `NMessageProvider`（/`NDialogProvider`）；不再注册 ElementPlus。
- n-modal/n-drawer 内容 teleport 到 document.body：断言用 document 查询（沿用 ProfileDrawer.spec 模式）。
- jsdom 中 n-modal 离场过渡不结束：close 后断言 DOM 移除按 OneTimePinDialog.spec 的"卸载后断言消失"约定（先实证 `update:show` 已触发）。
- VueFlow 节点组件独立挂载测试需 stub `Handle`（无 VueFlow 上下文时 Handle onMounted 崩溃）。
- SSE 流式渲染回归：mock useAgentStream 捕获回调，按协议事件序分片投喂，逐片断言渐进渲染，不做空壳 mock。
- 断言原则：优先 data-test/文本/行为断言；库内类名断言改语义选择器；禁止整段 skip。

## 浮层与层级

- naive 浮层（modal/drawer/popover）自增 z-index 基数 ≥2000；壳导航 sticky z=100；自绘悬浮件（AssistantLauncher=1500）必须低于 naive 基数，避免被浮层压住或反向遮挡。
- 下拉默认页内绝对定位即可（壳导航形成堆叠上下文）；引入 teleport 前先论证层级与主题（CSS 变量挂 root，teleport 不丢主题，但层级协调成本变高）。

## 审批/裁决表约定（AdminView 系；2026-08-30 空间管理者申请起沉淀）

- 运营者审批表沿用既有模式：NDataTable + `DataTableColumns<T>` h() 列渲染；行内动作「通过」直接调用裁决 API（approve 备注可选），「驳回」弹 NModal 填理由且提交钮在理由为空时 disabled（后端仍 422 兜底）。
- 申请/裁决状态徽章复用领域状态工具类：pending → `fg-badge--proposed`（空心"审批中"）、approved → `fg-badge--confirmed`（"已通过"）、rejected → `fg-badge--disputed`（"未通过"+平台备注），不新增颜色/样式。
- 队列只展示裁决所需最小数据（申请人名/类型/目标名），不做家庭敏感字段扩展；文案统一「已通过/未通过并留痕审计」。
- n-select 在 jsdom 测试中的选择走键盘路径（`.n-base-selection` 两次 Enter，见 SpaceGovernanceDialog.spec / MemberCreateWizard.spec）。
