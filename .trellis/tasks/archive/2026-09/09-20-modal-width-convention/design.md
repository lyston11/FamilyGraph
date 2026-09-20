# 技术设计：补齐缺失的卡片弹窗宽度

## 1. 根因与既有约定

naive 的 `preset="card"` 弹窗没有内置宽度上限：card preset 走 card 样式（`.n-card { width: 100% }`，无 max-width），外层 `n-modal-scroll-content` / `n-scrollbar-content` / `n-modal-body-wrapper` 也不限宽，所以 `.n-modal.n-card` 的 width 直接取视口宽。

仓库**已经**有一条应对约定（13 处遵循），写在组件自己的**非 scoped** `<style>` 块里：

```vue
<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='one-time-pin-dialog'] {
  width: min(420px, calc(100vw - 48px));
}
</style>
```

必须是非 scoped（弹窗 teleport 到 `body`，scoped 属性对不上），且靠 `data-test` 锚定根节点。

**缺口**：18 个 card 弹窗里 5 个没有这条声明，于是铺满视口。实测（1600px 视口）：

| 弹窗 | 实测宽度 |
|---|---|
| `family-space-join-dialog` | **1600px（= 视口）** |
| `execute-confirm-dialog` / `confirm-memory-dialog` / `memory-editor-dialog` / `suggestion-dialog` | 同为铺满（无任何宽度声明） |

## 2. 为什么不能用全局规则（已验证）

曾考虑在 `global.css` 加一条 `.n-modal.n-card { width: min(560px, …) }` 作为单一来源。**它是错的**：

- `.n-modal.n-card` 特异度 **(0,2,0)** > `[data-test='…']` **(0,1,0)**；
- 因此在文档里用运行实例注入该规则实测：既有 360px 的弹窗在注入后实测变 **560px**——13 处精心挑选的取值会被一次性覆盖。

（实测记录：`perComponentRule_before: "360px"` → `perComponentRule_afterGlobalRule: "560px"`。）

所以要沿用既有约定逐组件补齐，保持既有取值不动。

## 3. 取值

从既有尺度取（360/380/400/420/460/520/700），按内容量：

| 组件 | `data-test` | 内容 | 取值 | 依据 |
|---|---|---|---|---|
| `FamilySpaceJoinDialog` | `family-space-join-dialog` | 方向单选 + 关系词输入 + 空间单选 + 多段提示 | **520** | 与 `MemberCreateWizard` 同为多控件表单；比它少一步 |
| `MemoryEditorDialog` | `memory-editor-dialog` | 表单：标题 + 内容(3 行) + 类型选择 | **520** | 同上（有 3 行文本域） |
| `MemoryCandidateConfirmDialog` | `confirm-memory-dialog` | 确认：数字输入 + 范围选择 + 提示 | **460** | 与 `SpaceCreateDialog` 同量级 |
| `ActionCardItem` | `execute-confirm-dialog` | 纯确认：提示 + 按钮 | **420** | 与 `OneTimePinDialog`/`AddRelationDialog` 同量级 |
| `SuggestionReviewDialog` | `suggestion-dialog` | 详情 + 操作按钮 | **520**（保持现值） | 现为内联 `maxWidth: '520px'`，收敛为同形 CSS |

窄屏一律 `calc(100vw - 48px)`（既有 13 处同形）；375px 视口 → 327px，不溢出。

`SuggestionReviewDialog` 的收敛：删掉 `:style="{ maxWidth: '520px' }"`（内联样式只能表达 max-width，与 `width:` 约定不同形、也不受同一处约束），改为同形 CSS 块。取值不变（520），故视觉无变化。

## 4. 测试策略

新增源级契约 `src/components/member/__tests__/modal-widths.spec.ts`（或置于 `src/__tests__/`）：

1. **穷举**：用 `import.meta.glob` 读取全部 `src/{components,views}/**/*.vue`，凡含 `preset="card"` 的 `<NModal>` 块，必须能匹配到宽度声明（`width: min(<N>px, calc(100vw - 48px))`）。漏一个即失败——这是防止第 6 个漏网的关键，也是本次缺口的直接教训。
2. **锁定取值**：5 个补齐的弹窗断言各自取值（520/520/460/420/520）。
3. **既有取值不被改动**：抽查若干既有弹窗（360/380/400/420/460/700）断言值未变。
4. **不引入全局覆盖**：断言 `styles/global.css` 不含 `.n-modal` 宽度规则（防止有人再走那条已被证伪的路）。

jsdom 无布局引擎，故断言源码契约；**真实像素由运行实例实测**（记录在 implement.md）。

## 5. 不做的事

- 不新增全局宽度规则（已证伪）。
- 不改既有 13 个弹窗的取值。
- 不改弹窗交互、`data-test` 锚点、naive 主题变量或版本。
- 不把 `width: min(...)` 用 scoped 样式写（teleport 后 scoped 属性对不上，会静默失效）。
