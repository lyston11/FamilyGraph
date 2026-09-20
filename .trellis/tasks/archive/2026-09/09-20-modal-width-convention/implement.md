# 实施计划

## 规划与启动

- [x] 运行实例实测确认现象：`family-space-join-dialog` 在 1600px 视口下宽 1600px（= 视口），`max-width: none`。
- [x] 盘点全部 18 个 `preset="card"` 弹窗：**13 个已有** `[data-test='…'] { width: min(Npx, calc(100vw - 48px)) }` 约定，**5 个缺失**。
- [x] 证伪「全局 `.n-modal.n-card` 单一来源」方案：特异度 (0,2,0) > (0,1,0)，实测会把既有 360px 覆盖成 560px。
- [x] 用户确认取值方向；本次按内容量确定 5 个取值。
- [ ] 用户批准规划后 `task.py start`，进入隔离 worktree。

## 实现顺序

1. `FamilySpaceJoinDialog.vue`：加非 scoped `<style>` 块，`[data-test='family-space-join-dialog'] { width: min(520px, calc(100vw - 48px)); }`。
2. `MemoryEditorDialog.vue`：同上，`memory-editor-dialog` = 520。
3. `MemoryCandidateConfirmDialog.vue`：同上，`confirm-memory-dialog` = 460。
4. `ActionCardItem.vue`：同上，`execute-confirm-dialog` = 420。
5. `SuggestionReviewDialog.vue`：删 `:style="{ maxWidth: '520px' }"`，改为同形 CSS 块 `suggestion-dialog` = 520。
6. 新增源级契约测试 `src/components/member/__tests__/modal-widths.spec.ts`：
   - 穷举全部 `preset="card"` 弹窗，每个都必须有宽度声明（缺一即失败）；
   - 锁定 5 个取值；
   - 抽查既有取值未变（360/380/400/420/460/700）；
   - 断言 `global.css` 不含 `.n-modal` 宽度规则。
7. 更新 spec `frontend/component-guidelines.md`：把这条既有约定写成明文（非 scoped + `data-test` 锚定 + `min(Npx, calc(100vw - 48px))`），并说明「不要改用全局 `.n-modal.n-card`」及其特异度原因。

## 验证

- `cd frontend && npm run lint && npm run type-check`
- `cd frontend && npx vitest run src/components/member/__tests__/modal-widths.spec.ts`
- `cd frontend && npm test && npm run build`
- **运行实例实测**（worktree 起独立 vite，避免主检出 5173 仍在 main 上）：
  - 1600px 视口：5 个弹窗分别 ≈ 520/520/460/420/520，居中，不溢出；
  - 375px 视口：宽度 327px，不横向溢出；
  - 抽查既有弹窗取值未变（如 `kinship-correction-dialog` = 360、`space-create-dialog` = 460、`space-governance-dialog` = 700）。
- `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`（纯样式改动，作回归确认）。

## 回滚点

纯 CSS 声明；回退 5 个组件的 `<style>` 块即可，无逻辑与数据影响。
