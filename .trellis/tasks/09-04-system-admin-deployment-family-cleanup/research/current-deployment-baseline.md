# 子任务 4 研究：当前部署与家庭端后台痕迹基线

- `docker-compose.yml` 当前只有 `api`（`8000:8000`）、`web`（`8080:80`）、`agent` 三个服务；单一 web 构建自 `frontend/`，不存在 admin web 服务或独立网络。
- `frontend/vite.config.ts` 只有单一 Vite server：`/api` 代理到 `http://localhost:8000`；无 surface/admin 端口配置。
- `frontend/nginx.conf` 当前：`location /api/ → api:8000`，`location /internal/ → 404`，其余 SPA fallback；没有对 `/admin-api/*`、`/system-admin*`、`/admin*` 的 404 规则。
- `frontend/Dockerfile` 为单一 dist 构建（node:22-alpine build → nginx:1.27-alpine），后台前端需要独立 Dockerfile 与构建上下文。
- `frontend/src/App.vue` 在同一 app 内切换 `SystemAdminShell` 与 `AppShell`；`frontend/src/router/index.ts` 包含 `/system-admin`、`/system-admin/login`、`/force-change-pin`、`/admin` 路由；`frontend/src/stores/auth.ts` 使用共享 `fg.refresh_token` key 和 `principal_type` 主体分支；`frontend/src/api/admin.ts` 同时包含安全治理与旧 break-glass client。这些都是子任务 4 要从家庭端移除的痕迹。
- `README.md` 当前只描述家庭前端 `8080`/开发 `5173`/API `8000`；需要拆分普通用户部署文档与受限后台运维文档。
- 并行任务 `09-04-frontend-glass-cosmic-redesign` 正在修改 `frontend/` 多个视图与样式文件（含 `HouseholdCardView.vue`）；本任务的清理不得覆盖这些文件的非后台改动。
- 交付后验证基线：`docker compose config`、双前端 build/type-check/test、后端全量 pytest、family dist 禁止字符串扫描、8000/8002 路由矩阵、宿主对 8002 不可达。
