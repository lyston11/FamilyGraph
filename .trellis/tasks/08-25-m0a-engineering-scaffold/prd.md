# m0a 工程骨架与开发部署

> 父任务：[08-25-m0-scaffold-pin-auth](../08-25-m0-scaffold-pin-auth/prd.md)｜权威上下文：[HANDOFF.md](../../HANDOFF.md)、[architecture.md](../../spec/architecture.md)

## Goal

可运行的前后端工程骨架与开发/部署底座，为所有后续子任务提供地基。

## Requirements

- 单仓布局 `backend/`（FastAPI+SQLAlchemy+Alembic+pytest+ruff+mypy）+ `frontend/`（Vue3+Vite+TS+ESLint+vitest）+ 根 README。
- SQLite WAL + architecture.md §5 的四项 PRAGMA 启动统一设置；Alembic 初始迁移框架就绪。
- docker-compose：`api` + `web`(nginx 托管前端构建产物并反代 /api) + 数据卷（db/uploads/backups）；本地开发模式支持 uvicorn+vite 各自起。
- 健康检查端点 `GET /api/health`；前端登录页空壳路由跑通端到端代理。
- 质量门禁命令按 spec/backend、spec/frontend quality-guidelines 配置成脚本可直接执行。
- 版本锁定：pyproject.toml + package-lock.json，Compose 固定 Python/Node 镜像版本。

## Acceptance Criteria

- [ ] 全新克隆 `docker compose up --build` 全栈启动，health 返回 200。
- [ ] 后端 ruff/mypy/pytest 与前端 lint/type-check/test/build 门禁脚本全绿（空测试基线）。
- [ ] Alembic 可从空库生成并应用初始迁移；PRAGMA 四项生效可验证。
- [ ] README 包含本地开发与容器两种启动方式。

## Non-goals

- 认证业务逻辑（m0b）；任何业务表结构（各子任务自带迁移）。
