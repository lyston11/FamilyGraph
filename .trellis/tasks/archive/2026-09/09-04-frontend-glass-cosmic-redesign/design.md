# 前端高级星空底、透明磨砂卡片与圆形底部操作重构 Technical Design

## 1. 设计体系与 CSS Token 扩展

### 1.1 玻璃态与深邃光效 Token 规范
在 `tokens.css` 与 `tokens.ts` 中引入玻璃态和星系环境光标准变量：
- `--fg-glass-surface`: `rgba(255, 255, 255, 0.08)`（暗色） / `rgba(255, 255, 255, 0.72)`（亮色磨砂）
- `--fg-glass-surface-raised`: `rgba(255, 255, 255, 0.12)` / `rgba(255, 255, 255, 0.82)`
- `--fg-glass-border`: `rgba(255, 255, 255, 0.16)` / `rgba(0, 0, 0, 0.08)`
- `--fg-glass-border-glow`: `color-mix(in srgb, var(--fg-accent) 25%, transparent)`
- `--fg-glass-blur`: `blur(16px) saturate(180%)`
- `--fg-shadow-glass`: `0 8px 32px 0 rgba(0, 0, 0, 0.25)`

### 1.2 画布星空底层（Cosmic Background）
在 `AppShell.vue` 的 `.shell-main` 以及 `tokens.css` 中，组合三层静态光晕与星宿点阵：
```css
/* 深邃夜空基底 + 远近交叠星宿粒子 + 漫反射星云光 */
background-color: var(--fg-surface);
background-image:
  radial-gradient(ellipse 80% 60% at 50% -20%, color-mix(in srgb, var(--fg-accent) 12%, transparent), transparent 70%),
  radial-gradient(ellipse 60% 50% at 80% 100%, color-mix(in srgb, var(--fg-info) 10%, transparent), transparent 60%),
  radial-gradient(circle 1.5px at 30px 40px, var(--fg-dot) 100%, transparent),
  radial-gradient(circle 1px at 120px 180px, color-mix(in srgb, var(--fg-dot) 60%, transparent) 100%, transparent),
  radial-gradient(circle 1.8px at 260px 90px, var(--fg-dot) 100%, transparent);
background-size: 100% 100%, 100% 100%, 320px 320px, 240px 240px, 400px 400px;
```

## 2. 页面底部圆形小按钮（Circular Floating Action Dock）

### 2.1 底部操作 Dock 架构
将页面中原分散的底部或顶部操作（如编辑资料、空间管理、回到自己、缩放/适配、布局切换等）抽象并设计为底部悬浮的 **Circular Action Dock**：
- 容器采用胶囊状超高透毛玻璃栏（`backdrop-filter: blur(24px)`，圆角 999px，周围柔和环境光）；
- 操作项均为统一规格的圆形按钮：
  - 常规尺寸：42px × 42px（移动端自动扩展触控热区为 44px 以上）；
  - 图标与微文字居中或通过 Tooltip 浮动展示；
  - 悬停动效：微弹缩放 `transform: translateY(-3px) scale(1.08)` 并带 Accent 光晕扩散。

## 3. 家庭空间大卡片（Household Card）架构优化

- **入场与大卡片结构**：
  - 登录后首屏展示大卡片，外框呈现高通透水晶玻璃边框与漫射阴影；
  - 退出按钮设计为顶部或底部显著的精致圆形退回按钮（带“退出至家族树”提示）；
- **成员网格与聚焦**：
  - 保持中心聚焦与向四周自然衰减虚化；
  - 每个成员小卡片为流光玻璃卡片，本人卡片环绕金色或主色星环（Accent Ring）。

## 4. 家族空间树：一颗颗星宿节点（Constellation Nodes）

- **节点设计（MemberNode.vue）**：
  - 摆脱传统方形呆板卡片，改造为圆形/星体水晶球质感：
    - 中心展示圆形头像或姓名首字，环绕精细发光星环（Orb Ring）；
    - 下方悬挂精致微标签（称谓与姓名）；
    - 本人节点高亮发光、直系亲属明亮透彻、远亲及汇总节点柔光呈现；
- **关系轨道（Edges）**：
  - 画布中的连接线采用细腻的星轨半透明虚实结合线条，产生如星系轨道图般的浩瀚艺术美感。
- **底部画布操作**：
  - 原左上角的缩放、回到自己、布局切换等收敛到家族树底部的悬浮圆形小按钮群。

## 5. 系统后台前端（system-admin-frontend）同套视觉设计（2026-09-05 范围扩展）

- **Token 落点**：后台无 tokens.ts 运行时注入体系，颜色单一来源是其自有 `src/styles/main.css` 的 `:root { --ag-* }`。新增玻璃/星空变量（`--ag-glass-bg`、`--ag-glass-bg-strong`、`--ag-glass-border`、`--ag-glow`）只在该处定义，组件与视图只消费变量（与「组件内不写死色值」红线同构）。
- **星空底座**：`body` 上叠三层静态 `radial-gradient`（顶部主色星云漫射、右下辅助色微光、两组周期性星点阵 `background-size` 平铺），纯 CSS 无脚本；`color-scheme: light` 不变。
- **玻璃材质**：`.admin-header`（sticky + 半透明磨砂 + 发丝下边框）、`.ag-card`/`.ag-metric`/`.auth-card`/`.ag-card-row`（`--ag-glass-*` + `backdrop-filter: blur(16px) saturate(160%)` + 内高光描边）；表格容器保持横向滚动与对比度，不加 blur 到单元格层。
- **隔离红线**：不 import 家庭端任何模块；不新增依赖；不改路由与业务逻辑。
- **克制**：后台不做入场动画堆叠，仅保留 hover 过渡；`prefers-reduced-motion` 全局降级沿用家庭端模式。

## 6. 兼容性与测试守卫
- 保持 DOM 测试选择器稳定（`[data-test=...]`），确保既有 490+ 单元测试不破坏；
- 保持 Naive UI 组件及响应式 375px 断点无横向滚动条；
- 动效支持 `prefers-reduced-motion` 优雅降级。
