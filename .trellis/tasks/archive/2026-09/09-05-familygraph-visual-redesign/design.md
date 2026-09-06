# Design: FamilyGraph 双前端高级视觉重设计

## Boundaries

- 家庭端：`frontend/src/styles/tokens.ts`、`tokens.css`、`global.css`、`components/shell/AppShell.vue`、`views/HouseholdCardView.vue`、`views/FamilyTreeView.vue`、必要的画布节点样式。
- 后台：`system-admin-frontend/src/styles/main.css`、`components/AdminShell.vue`（仅在确有壳层结构需要时调整）。
- 不越过前端视觉层边界，不触碰服务端或状态管理。

## Visual System

家庭端新增画布语义 token：`canvas-surface`、`canvas-surface-raised`、`canvas-ink`、`canvas-muted`、`canvas-line`、`canvas-star`、`canvas-glow`。纸墨主题保留暖色内容区，家族树画布使用深蓝星空；清雅主题保留冷白内容区，画布同样使用深色星空但采用更冷的光晕。所有组件仅消费变量。

全局 body 与 `.shell-main` 使用多层径向光晕和点阵，`.fg-glass-card`、顶栏、家庭 hero、成员区域和关系面板统一使用玻璃 token，并提供不透明 fallback。

家庭首页在现有 `v-else-if="card !== null"` 分支内增加 `family-space-hero`，将空间身份、管理员状态、成员数和版本集中到首屏大卡片；原有成员投影和底部 FAB Dock继续保留。

家族树 `.canvas-wrap` 改用画布专用深色背景和星点层，Vue Flow 控制器与关系线改消费画布对比 token；节点仍是浅色玻璃浮层以保证姓名和敏感状态的可读性。

后台 `main.css :root` 改为深色石墨/海军蓝色板，保留原有 `--ag-*` 命名和组件选择器。星点、星云、玻璃透明度和状态软底都从根 token 派生，业务视图不改。

## Compatibility

- `paper`/`modern` 名称和 `ui` store 契约不变。
- 所有 `data-test` 锚点、按钮文案、路由和 API 调用保持不变；hero 仅新增锚点。
- 后台仍只有 `/admin-api`，不共享家庭端 token、store 或组件。

## Rollback

视觉改动可整体通过 git 回滚；不涉及迁移和运行时数据。若深色后台对比度不达标，只需回调 `main.css :root`，不需要改业务视图。
