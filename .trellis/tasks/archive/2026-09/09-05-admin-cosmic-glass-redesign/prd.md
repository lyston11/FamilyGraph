# 系统管理后台星空玻璃视觉深度重构 PRD

## 1. 目标与背景

09-04 任务为 system-admin-frontend 做的"克制版"玻璃化已部署（8081），但效果过弱：
星点 1.2px、星云 12% 透明度、66% 白玻璃底在浅灰背景上肉眼几乎不可辨，实际观感
仍是素白后台（用户判定"太土"）。本次重构将家庭端的星空玻璃设计语言**完整移植**
到后台，使两个前端呈现同一档次的视觉质感。

## 2. 设计原则

### 2.1 可感知的星空底（Cosmic Background）
- 星云漫射透明度提升到肉眼明确可见（主色星云 ≥16%，辅助星云 ≥10%）；
- 星点阵加大加密（1.5~2px，三组周期平铺），叠加轻微暗角（vignette）增强纵深；
- 登录页（auth-page）单独加强：更大星云 + 更密星点。

### 2.2 真实毛玻璃卡片（Glassmorphism）
- `.ag-card` / `.ag-metric` / `.auth-card` / `.ag-card-row`：半透明底（≤0.7）+
  `backdrop-filter: blur(20px) saturate(160%+)` + 发丝高光描边 + 环境光晕阴影；
- 卡片入场动画（cardSlideIn 类）与 hover 上浮光晕，`prefers-reduced-motion` 降级。

### 2.3 品牌感与标题层级
- 页面标题 `.ag-page-title`、品牌名 `.admin-brand` 使用主色渐变文字
  （background-clip: text，同家庭端 card-title 手法）；
- 指标数值 `.ag-metric-value` 渐变主色。

### 2.4 操作控件质感
- 登录主按钮 / `.ag-btn-primary` 渐变实底 + hover 光晕；
- 表格：表头玻璃实底、行 hover 主色微底、容器保持内部横向滚动（375px 红线）；
- 导航激活态、`.ag-tag` 徽章、`.ag-tab` 激活态随新 token 联动。

## 3. 约束（红线）

1. **隔离红线**：零家庭依赖（不 import 家庭端任何模块/token）；不新增依赖。
2. **颜色红线**：组件与视图无局部样式文件，颜色一律走 `src/styles/main.css`
   的 `:root { --ag-* }` 单一来源；main.css 之外不出现写死色值。
3. **可读性红线**：表格数据区、表单控件对比度满足 WCAG AA；表格单元格不加 blur；
   信息密度不降低（后台以效率优先，动效仅入场/hover，克制于家庭端）。
4. **布局红线**：不改任何视图模板与路由；375px 无页面级横向滚动（ag-table-wrap
   内部滚动语义保持）。

## 4. 验收标准（Acceptance Criteria）

1. 截图可感知：登录页、概览页、空间详情页的星空底、玻璃卡片、渐变标题肉眼明确
   可辨（对比 09-04 版本）；
2. 后台全部 13 个视图经共享类自动获得新质感，无逐视图补丁；
3. `npm run lint` + `npm run type-check` + `npm run build` 零缺陷；
4. 重建 admin-web 镜像并重新部署后，运行容器内的 CSS 产物包含新 token 与新规则
   （可 grep 验证），8081 端到端可登录；
5. 隔离与颜色红线复核通过（无家庭 import、main.css 外无写死色值）。
