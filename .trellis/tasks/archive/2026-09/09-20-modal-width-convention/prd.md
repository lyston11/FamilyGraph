# 补全卡片弹窗宽度：5 个缺失宽度声明的弹窗

## Goal

仓库已有一条弹窗宽度约定（`[data-test='…-dialog'] { width: min(Npx, calc(100vw - 48px)) }`，写在组件自己的非 scoped `<style>` 块里），18 个 `preset="card"` 弹窗中 13 个遵循、**5 个缺失**。缺失的弹窗没有宽度上限，`.n-modal.n-card` 的 `width: 100%` 直接取视口宽，宽屏上铺满全屏。本任务补齐这 5 个，并让约定可被测试锁定。

## Requirements

### R1 沿用既有约定，不引入第二套机制

- 新增宽度一律写成组件内非 scoped 样式块 `[data-test='…'] { width: min(Npx, calc(100vw - 48px)) }`，与既有 13 处完全同形。
- **不新增全局 `.n-modal.n-card` 宽度规则**：`.n-modal.n-card`（特异度 0,2,0）会压过 `[data-test='…']`（0,1,0），把既有 13 处的逐个取值全部改写成同一个值——那是回归，不是修复。
- 取值从既有尺度取：360 / 380 / 400 / 420 / 460 / 520 / 700。

### R2 补齐的 5 个弹窗与取值

| 组件 | 弹窗 | 内容 | 取值 |
|---|---|---|---|
| `FamilySpaceJoinDialog` | `family-space-join-dialog` | 方向单选 + 关系词输入 + 空间单选 + 提示 | 520 |
| `MemoryEditorDialog` | `memory-editor-dialog` | 表单：标题/内容（3 行）/ 类型选择 | 520 |
| `MemoryCandidateConfirmDialog` | `confirm-memory-dialog` | 确认：数字输入 + 范围选择 + 提示 | 460 |
| `ActionCardItem` | `execute-confirm-dialog` | 纯确认：提示 + 按钮 | 420 |
| `SuggestionReviewDialog` | `suggestion-dialog` | 详情 + 操作按钮 | 520 |

- `SuggestionReviewDialog` 现有 `:style="{ maxWidth: '520px' }"` 收敛为同形 CSS 约定（去掉内联 style 与 class 依赖），保持 520 不变。
- 窄屏一律 `calc(100vw - 48px)`，与既有 13 处一致（不是 32px）；375px 视口下为 327px，不溢出。

### R3 不改行为

- 弹窗显示/关闭、焦点陷阱、遮罩、`data-test` 锚点、页脚按钮布局全不变。
- 不改已遵循约定的 13 个弹窗的任何取值。
- 不改 `naive-themes.ts` 与 naive 版本。

### R4 回归范围

- 新增源级契约测试：所有 `preset="card"` 弹窗必须带宽度声明（`min(Npx, calc(100vw - 48px))` 或等价内联），防止第 6 个漏网；并锁定 5 个补齐后的取值。
- 只运行受影响的前端定向测试与必要静态检查。

## Acceptance Criteria

- AC1：5 个缺失弹窗补上 `[data-test]` 宽度约定，取值按 R2，宽屏不再铺满视口。
- AC2：既有 13 个弹窗的宽度取值一个都不变（本改动不得引入任何全局宽度规则）。
- AC3：窄屏 375px 下宽度为 327px 且不横向溢出。
- AC4：新增源级契约测试能枚举全部 `preset="card"` 弹窗并断言每个都有宽度声明；漏一个即失败。
- AC5：受影响 frontend 定向检查通过；未运行的高成本检查如实记录。
