# 双前端双 API 部署隔离与家庭端后台痕迹清理：实施计划

> 前置依赖：子任务 1（admin listener）、子任务 2（读模型）、子任务 3（后台前端）全部完成后执行。

## 1. Preconditions

- [ ] 读取父任务 PRD/design/notes、本任务 PRD/design 与部署基线研究。
- [ ] 确认 `system-admin-frontend/` 已存在并可通过子任务 3 的构建与测试。
- [ ] 确认 8000 已无 admin 路由、8002 admin listener 可独立启动（子任务 1 交付）。
- [ ] 检查当前工作树的并行修改（尤其是 `09-04-frontend-glass-cosmic-redesign` 触及的 `frontend/` 文件）；只清理后台痕迹相关文件，不回退视觉改版 WIP。

## 2. Ordered implementation

- [ ] Docker Compose：新增 `admin-web` 服务（构建 `system-admin-frontend/`，绑定 `127.0.0.1:8081:80`，admin 网络）；`api` 保持发布 `8000`，不发布 8002；family web 与 admin web 分网络或以 Nginx location 隔离。
- [ ] 环境变量：`ADMIN_JWT_SECRET/ISSUER/AUDIENCE`、`ADMIN_BIND_HOST`（默认回环）只注入 api 容器；不作为 build arg、不进入任何前端 env。
- [ ] 家庭 `frontend/` 清理：删除 `SystemAdminView.vue`、`SystemAdminLoginView.vue`、`SystemAdminShell.vue`、`/admin` 路由、后台 API client、后台 types 与文案；删除 auth store 中的 system_admin 主体分支与共享 refresh 逻辑。
- [ ] 家庭 router：移除后台路由与后台 redirect 逻辑；`/system-admin*`、`/admin*` 深链落入普通 404，不跳转后台。
- [ ] Nginx：family 配置增加 `location ^~ /admin-api/ { return 404; }` 与后台路径 404，`/api/` 只代理 8000；admin 配置 `/admin-api/` 只代理 8002、`/api/` 返回 404；两者均拒绝 `/internal/`。
- [ ] README/部署文档：普通用户部署部分不出现后台端口、入口或凭据说明；后台启动/凭据文件/VPN 访问写进仅运维可见章节。
- [ ] 静态断言：family dist 与 source map 中不得出现 `system_admin`、`platform_operator`、`/admin-api`、`5174`、`8081`、SystemAdmin 组件等字符串；依赖图不得引用后台模块。
- [ ] 路由矩阵验证脚本/测试：family web → 8000 允许、→ 8002 拒绝；admin web → 8002 允许、→ 8000 拒绝；宿主直连 8002 拒绝。

## 3. Verification

```bash
docker compose config -q
cd frontend && npm run build && npm run type-check && npm run lint && npm test
cd system-admin-frontend && npm run build && npm run type-check && npm test
cd backend && .venv/bin/python -m pytest -q
# 部署后
curl -si http://127.0.0.1:8080/admin-api/health   # 普通 404
curl -si http://127.0.0.1:8081/                   # 后台前端
curl -si http://127.0.0.1:8002/admin-api/health   # 宿主默认不可达
```

静态扫描（示例）：

```bash
grep -rE "system_admin|platform_operator|admin-api" frontend/dist/ && echo "LEAK" || echo "OK"
```

## 4. Stop points

- 发现家庭 bundle/路由/OpenAPI 仍含后台字符串或组件：停止发布，回到清理步骤。
- 需要把 8002 映射到宿主公网或让家庭 web 反代 admin API 才能工作：停止并重新设计。
- 需要覆盖 `HouseholdCardView.vue` 或 glass-cosmic-redesign WIP 才能继续：停止，等待并行任务合并。
- admin 前端构建依赖家庭 frontend 代码：停止，改为独立实现。

## 5. Rollback

- 可单独停用 `admin-web` 与 admin listener，家庭 web/API 不受影响；不得把后台 dist 合回家庭镜像、不得把 admin API 临时代理到 8000 作为回滚。
- 家庭前端清理如引发回归，可按文件回退本次清理提交，但不得恢复后台路由/组件到家庭 bundle。
