# FamilyGraph

现代家谱协作 Web 平台：以每个人为第一人称维护家庭空间，家庭空间相连自然涌现家族视图。

技术栈：Vue 3 + Vite + TypeScript（前端）｜FastAPI + SQLAlchemy + SQLite(WAL)（后端）｜Docker Compose（部署）。

## 仓库布局

```
backend/    FastAPI 应用 + Alembic 迁移 + pytest/ruff/mypy 门禁
frontend/   Vue3 + Vite + TS 应用 + eslint/vitest 门禁
```

## 启动方式一：容器模式（推荐）

```bash
# 可选：正式部署前设置会话密钥（本地试用可用 compose 默认值）
echo "SECRET_KEY=$(openssl rand -hex 32)" > .env

docker compose up --build -d

curl -f http://localhost:8000/api/health   # {"status":"ok"}
curl -f http://localhost:8080/api/health   # 经 nginx 反代，同样返回 ok
# 浏览器打开 http://localhost:8080
```

数据落盘于命名卷 `app_data`（容器内 `/data`：`db/` SQLite 主库+WAL 文件、`uploads/`、`backups/`）。

## 启动方式二：本地开发模式

前置要求：Python ≥ 3.12、Node ≥ 22。

```bash
# 终端 1 —— 后端
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export SECRET_KEY=$(openssl rand -hex 32)   # 必需；缺失时应用拒绝启动
uvicorn app.main:app --reload               # http://localhost:8000/api/health

# 终端 2 —— 前端
cd frontend
npm ci
npm run dev                                 # http://localhost:5173，/api 由 vite 代理到 :8000
```

## 数据库迁移（Alembic）

```bash
cd backend
alembic upgrade head          # 从空库应用到最新
alembic downgrade base        # 回滚到空库
alembic revision -m "change"  # 生成新迁移（业务表结构随各子任务迁移引入）
```

数据库 URL 由 `app/config.py` 统一提供（默认 `<cwd>/data/db/app.db`，可用 `DATA_DIR` 覆盖）；启动时自动设置 PRAGMA：`foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL`。

## 质量门禁

```bash
cd backend  && ruff check . && ruff format --check . && mypy app/ && pytest
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
```

## 备份约束（重要）

SQLite 运行于 WAL 模式。**禁止在服务运行期直接 `cp` 主库文件**——会得到不一致快照。
备份统一走 SQLite online backup API（`python -m app.backup`，后续任务落地），见 HANDOFF AD-6。

## 安全约定

- `SECRET_KEY` 必须经环境变量提供，缺失时后端拒绝启动；compose 默认值仅供本地开发。
- nginx 不直接托管 uploads 目录；附件下载一律走后端授权端点（architecture.md §6/§9）。
