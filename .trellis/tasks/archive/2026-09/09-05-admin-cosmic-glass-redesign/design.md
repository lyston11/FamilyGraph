# 系统管理后台星空玻璃视觉深度重构 Technical Design

## 0. 参照系

家庭端已验证的视觉手法（frontend/src，仅供参照模式，禁止 import）：
- 渐变标题：`linear-gradient(135deg, var(--accent) 0%, color-mix(in srgb, var(--accent) 70%, var(--ink) 30%) 100%)` + `-webkit-background-clip: text` + `background-clip: text` + transparent 填充；
- 玻璃卡：`background: var(--glass-surface)` + `backdrop-filter: blur(20px) saturate(180%)` + 1px 玻璃描边 + 三层阴影（环境投影 / accent 8~12% 光环 color-mix / inset 白 25% 高光）+ `cardSlideIn` 弹性入场（cubic-bezier(0.34,1.56,0.64,1)）；
- hover：`translateY(-2~-4px)` + `0 0 16~24px var(--glow)`；
- 星空底：body 级多层 radial-gradient（星云 + 三组星点平铺）+ `background-attachment: fixed`。

## 1. Token 扩展（唯一落点：system-admin-frontend/src/styles/main.css :root）

新增/调整（全部双用途只在此处定义字面量）：

```css
--ag-nebula: rgba(47, 94, 168, 0.18);        /* 12% → 18%，顶部主星云 */
--ag-nebula-alt: rgba(46, 125, 79, 0.10);    /* 7% → 10%，右下辅星云 */
--ag-star: rgba(47, 94, 168, 0.5);           /* 0.38 → 0.5 */
--ag-star-dim: rgba(47, 94, 168, 0.28);      /* 0.2 → 0.28 */
--ag-glass-bg: rgba(255, 255, 255, 0.6);     /* 0.66 → 0.6，更透 */
--ag-glass-bg-strong: rgba(255, 255, 255, 0.78);
--ag-vignette: rgba(31, 39, 51, 0.06);       /* 新增：暗角 */
--ag-accent-grad: linear-gradient(135deg, #2f5ea8 0%, #4a7ec4 55%, #2e7d4f 130%);
                                             /* 新增：品牌渐变（主色→亮主色→收尾绿） */
```

## 2. body 星空底（加深加密）

background-image 五层 + 暗角：
1. `radial-gradient(ellipse 90% 60% at 50% -15%, var(--ag-nebula), transparent 72%)`
2. `radial-gradient(ellipse 60% 50% at 92% 102%, var(--ag-nebula-alt), transparent 62%)`
3. 星点三组（1.5px/1.2px/2px，tile 240/190/320px，亮暗交替）
4. 暗角：`radial-gradient(ellipse 120% 120% at 50% 40%, transparent 60%, var(--ag-vignette) 100%)`
保持 `background-attachment: fixed`。

## 3. 各表面处理（全部在 main.css，模板零改动）

| 类 | 处理 |
|---|---|
| `.admin-header` | 保持 sticky 玻璃；`.admin-brand` 加渐变文字（--ag-accent-grad + background-clip） |
| `.admin-nav a` | 圆角胶囊；active 主色 soft 底 + 底部 2px accent 渐变线 |
| `.ag-page-title` | 渐变文字（同 brand）；`.ag-page-subtitle` 不变 |
| `.ag-card` | `--ag-glass-bg` + blur(20px) saturate(170%) + 三层阴影 + `cardSlideIn` 入场 |
| `.ag-card-row` | 玻璃底 + hover 主色 4% 微底 + translateY(-1px) |
| `.ag-metric` | 玻璃 + hover 上浮光晕；`.ag-metric-value` 渐变文字 |
| `.auth-page` | 独立加强星空（更大星云 24% + 密星点） |
| `.auth-card` | 玻璃 blur(24px) + 光晕阴影 + 入场动画 |
| 登录主按钮 | `.auth-card .form-actions button`（或既有主按钮类）主色渐变实底 + 白字 + hover 光晕 + 按压回弹 |
| `.ag-table` | th 玻璃实底 + 字距；tbody 行 hover 主色 4% 底；`.ag-table-wrap` 不变（内部滚动） |
| `.ag-tag-*` | 沿用语义色 token，仅微调内边距 |

## 4. 动效与降级

- 入场动画统一 `cardSlideIn 0.5s cubic-bezier(0.34,1.56,0.64,1)`，卡片 stagger ≤0.2s；
- 现有 `@media (prefers-reduced-motion: reduce)` 块扩展覆盖新动画（动画/位移归零）；
- `@supports not (backdrop-filter: blur(12px))` 降级块保持并覆盖 `.ag-card-row`。

## 5. 红线自查清单

- 颜色字面量只出现在 main.css `:root`（渐变 `--ag-accent-grad` 亦定义于此）；
- 模板/路由/业务逻辑零改动；无新增依赖；不引用家庭端任何文件；
- 表格 th/td 不加 backdrop-filter；正文对比度不降。
