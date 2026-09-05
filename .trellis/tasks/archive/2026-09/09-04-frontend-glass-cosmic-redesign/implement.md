# 前端高级星空底、透明磨砂卡片与圆形底部操作重构 Implementation Plan

## 实施步骤与检查清单

- [x] 1. 全局设计 Token 与星空底座扩展
  - [x] 1.1 更新 `tokens.ts` 与 `tokens.css`，加入玻璃态表面色、高光边缘、星宿点阵变量
  - [x] 1.2 改造 `AppShell.vue` 壳层：主区强化深邃星空底，导航栏与顶部栏采用高级半透明磨砂毛玻璃材质
  - [x] 1.3 验证基础构建与页面渲染正常
- [x] 2. HouseholdCardView（家庭空间大卡片）高级磨砂质感与退出交互
  - [x] 2.1 整体卡片升级为悬浮水晶玻璃大卡片（`backdrop-filter`、微光描边）
  - [x] 2.2 底部引入圆形小按钮浮动操作栏（编辑资料、待办通知、进入家族树收敛为底部精致圆钮，`.fg-fab-btn`）
  - [x] 2.3 退出至家族树操作升级为醒目的圆形水晶返回按钮（accent FAB + title/aria-label）
  - [x] 2.4 运行 `src/views/__tests__/household-card.spec.ts` 验证测试
- [x] 3. FamilyTreeView 家族空间树：星宿节点与星轨连线
  - [x] 3.1 改造 `MemberNode.vue`，实现如星宿/水晶体般的圆形发光节点（自身引力光环、直系亲属微光环）
  - [x] 3.2 优化家族树画布背景与连线样式，实现星轨与荧光路径质感
  - [x] 3.3 家族树底部工具条改造为居中悬浮的圆形小按钮 Dock（适应画布/回到自己/重新加载/图例改圆形 FAB，新增「返回家庭卡」accent FAB；布局切换保留 radio 分段）
  - [x] 3.4 优化 `RelationshipDetailPanel.vue`，呈现透明微光抽屉质感（含 @supports 降级）
  - [x] 3.5 运行 `src/views/__tests__/family-tree.spec.ts` 验证测试
- [x] 4. 全局测试与构建回归
  - [x] 4.1 运行 `src/views/__tests__/responsive-375.spec.ts` 检查 375px 移动端适配与无横向滚动
  - [x] 4.2 运行前端全量单元测试 `npm run test`
  - [x] 4.3 运行 `npm run type-check` 与 `npm run build` 确保类型与构建零缺陷
- [x] 5. 系统后台前端（system-admin-frontend）同套星空玻璃视觉（2026-09-05 范围扩展）
  - [x] 5.1 `src/styles/main.css`：新增 `--ag-glass-*` 玻璃 token 与 body 三层星空底（纯 CSS radial-gradient）
  - [x] 5.2 `.admin-header` sticky 磨砂玻璃化；`.ag-card`/`.ag-metric`/`.auth-card`/`.ag-card-row` 玻璃材质统一
  - [x] 5.3 隔离红线复核：零家庭依赖 import、组件无写死色值、无新增依赖
- [x] 6. 双前端验证回归
  - [x] 6.1 家庭端：`npm run lint` + `npm run type-check` + `npm run test` + `npm run build`
  - [x] 6.2 后台前端：`npm run lint` + `npm run type-check` + `npm run build`（当前无单测套件）

## 补充记录（2026-09-05 续作）

- 颜色红线复核：本任务改动的 .vue 组件与 tokens.css 中所有 rgba()/hex 写死色值已全部
  改为 `color-mix(in srgb, var(--fg-ink|--fg-surface-raised) N%, transparent)` 派生；
  后台 `--ag-*` token 定义处（main.css :root）按其「token 单一来源」架构保留字面量。
- 测试守卫适配：household-card.spec 的 `.exit-button { min-height: 44px }` 源级契约
  通过保留类名 + min-height 声明满足（未改测试）。
- 家族树测试的 layout-switch radio 结构与移动端 `.toolbar` nowrap/auto 契约均保留。

## 验证指令
```bash
cd frontend && npm run type-check
cd frontend && npx vitest run src/views/__tests__/household-card.spec.ts
cd frontend && npx vitest run src/views/__tests__/family-tree.spec.ts
cd frontend && npx vitest run src/views/__tests__/responsive-375.spec.ts
cd frontend && npm run test
cd frontend && npm run build
cd system-admin-frontend && npm run type-check
cd system-admin-frontend && npm run build
```
