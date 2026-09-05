# 系统管理后台星空玻璃视觉深度重构 Implementation Plan

## 执行清单

- [x] 1. Token 与星空底强化（`src/styles/main.css`）
  - [x] 1.1 `:root` 新增 `--ag-vignette`、`--ag-accent-grad`，调深 `--ag-nebula/-alt`、`--ag-star/-dim`，调透 `--ag-glass-bg/-strong`
  - [x] 1.2 body 星空底重写：双层星云 + 三组加密星点 + 暗角，保留 `background-attachment: fixed`
- [x] 2. 壳层与导航
  - [x] 2.1 `.admin-brand` 渐变文字；`.admin-nav a` 胶囊化 + active 底部渐变线
  - [x] 2.2 `.admin-header` 玻璃参数对齐（blur 20px saturate 170%）
- [x] 3. 卡片/指标/表格/登录
  - [x] 3.1 `.ag-card` / `.ag-card-row` / `.ag-metric` 真玻璃 + 三层阴影 + 入场动画 + hover 光晕
  - [x] 3.2 `.ag-page-title` / `.ag-metric-value` 渐变文字
  - [x] 3.3 `.ag-table` th 玻璃实底 + 行 hover 主色微底（单元格不加 blur）
  - [x] 3.4 `.auth-page` 加强星空 + `.auth-card` blur(24px) 光晕 + 登录主按钮渐变实底
  - [x] 3.5 `.ag-tab` / `.ag-tag` 激活与语义徽章随 token 联动微调
- [x] 4. 降级与红线
  - [x] 4.1 `prefers-reduced-motion` 块覆盖全部新增动画；`@supports not (backdrop-filter…)` 覆盖 `.ag-card-row`
  - [x] 4.2 红线复核：main.css 外无写死色值、无家庭 import、无新增依赖、模板零改动
- [x] 5. 验证与部署
  - [x] 5.1 `npm run lint` + `npm run type-check` + `npm run build` 零缺陷
  - [x] 5.2 重建 admin-web 镜像并 `docker compose up -d`，容器内 CSS 产物 grep 验证新 token，8081 端到端登录

## 验证指令

```bash
cd system-admin-frontend && npm run lint && npm run type-check && npm run build
docker compose build admin-web && docker compose up -d admin-web
docker exec familygraph-admin-web-1 sh -c "grep -l 'ag-accent-grad\|ag-vignette' /usr/share/nginx/html/assets/*.css"
```

## 附录一：对比度核验（quality-guidelines 门禁）

`--ag-accent-grad` 初始中间色 `#4a7ec4` 白字 4.14:1（<4.5 不达 AA），已替换为
`#3d6cb0`（WCAG 相对亮度公式计算）：白字于其上 5.29:1；其色字落于页面底
#f3f5f8 上 4.84:1——渐变按钮与渐变文字全程 ≥4.5:1（正文 AA）。
