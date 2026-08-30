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
# 正式部署前设置两个强随机密钥（Agent Runtime 也依赖第二个）
cat > .env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
AGENT_SERVICE_SECRET=$(openssl rand -hex 32)
EOF
chmod 600 .env

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

- `SECRET_KEY` 与 `AGENT_SERVICE_SECRET` 必须经环境变量提供强随机值；Compose 不提供默认密钥，缺失时启动失败。
- nginx 不直接托管 uploads 目录；附件下载一律走后端授权端点（architecture.md §6/§9）。
- Agent sidecar 不读取 Provider API key，也不直连上游；所有模型请求经 API 容器 ProviderGateway 出网。
- 云 Provider 门禁在代码中固定启用，只允许使用下方 `liu-dada/gpt-5.6-sol` 的 Pi
  profile；不存在可由部署环境关闭的绕过开关。本地 Provider 仍可单独注册。

### Pi Provider 配置（首版）

首版运行时对齐本机 Pi 的 `liu-dada / gpt-5.6-sol` profile：

```json
{
  "name": "liu-dada",
  "kind": "openai_compatible",
  "api": "openai-responses",
  "base_url": "https://api.liu-dada.com/v1",
  "allowed_models": ["gpt-5.6-sol"],
  "context_window": 272000,
  "max_tokens": 60000,
  "reasoning": true,
  "input_modalities": ["text", "image"],
  "thinking_levels": ["low", "medium", "high", "xhigh", "max"]
}
```

通过 `/api/admin/agent/providers` 提交上述非敏感字段，并在创建请求的 `secret` 字段注入 API key。密钥只会以 secretbox 密文存入后端，响应只返回 `has_secret`；不要把 key 写入仓库、日志、Trellis 文档或 Agent 容器环境。随后用 `/api/admin/agent/spaces/{space_id}/provider-settings` 选择 `gpt-5.6-sol` 并设置 `cloud_allowed=true`。

---

## 备份与恢复（重要）

**备份（一条命令）**：

```bash
docker compose exec api python -m app.backup
# 产物：/data/backups/familygraph-YYYYmmdd-HHMMSS.tar.gz（含数据库快照 + uploads）
# 宿主机直接取：docker cp <api容器>:/data/backups ./
```

⚠️ **禁止运行期直接 `cp` 主库文件**——SQLite 运行在 WAL 模式，直接复制会得到不一致的快照。一律使用上面的 online backup 命令。

**恢复演练**：解包 tar 取出 `.db` 文件 → 替换数据卷中的 `db/app.db`（先停 api 服务）→ 重启后自动通过完整性校验。验证命令：

```bash
sqlite3 app.db "PRAGMA integrity_check"   # 应输出 ok
```

## 迁移到云服务器（迁云清单）

1. 云服务器安装 Docker + Docker Compose。
2. `git clone` 本仓库 → 配置 `.env`：`SECRET_KEY=<openssl rand -hex 32>`、`AGENT_SERVICE_SECRET=<openssl rand -hex 32>`、`DATA_DIR=/data`，并执行 `chmod 600 .env`。
3. `docker compose up --build -d` → 首启页面初始化管理员（一次性 PIN，立即截图保存）。
4. 数据迁移：本机执行备份 → 把 tar 包传服务器 → 按上文恢复流程导入数据卷 → 重启。
5. 域名：DNS A 记录指向服务器 IP；HTTPS 二选一：
   - 方案 A（推荐）：Caddy 反代 80/443，自动签发 Let's Encrypt；
   - 方案 B：certbot + nginx 手动配置证书。
6. 定期备份建议：crontab 每日执行备份命令，并把 `/data/backups` 同步到对象存储。

---

## 运维手册（V2.6 发布治理）

### 功能开关与 kill switch

所有 V2 功能默认关闭，可通过环境变量逐层启用（compose `.env` 或 `docker compose` 的 `environment` 段）：

| 功能 | 环境变量 | 默认 | 说明 |
|------|----------|------|------|
| Agent Runtime | `AGENT_RUNTIME_ENABLED` | `0` | 关闭时 `/internal/agent/*` 一律 503 |
| 关系智能 | `RELATIONSHIP_INTELLIGENCE_ENABLED` | `0` | 关闭时关系解析端点 503 |
| Steward | `STEWARD_ENABLED` | `0` | 关闭时 ActionCard 入口 503 |
| Memory/RAG | `MEMORY_ENABLED` / `RAG_ENABLED` | `0` | 关闭时无候选/检索，工具路径保留 |
| 受控联网 | `CONTROLLED_WEB_ENABLED` | `0` | 平台总开关；空间还需 owner/admin 单独 opt-in |

**紧急 kill switch**：任何 Web 安全问题先全局关闭——设 `CONTROLLED_WEB_ENABLED=0` 重启 api，或经管理后台把平台配置 `enabled` 置 false。移除工具披露即可，不影响本地 Assistant/Steward 和 v1 家谱功能。

部署故障：停止 agent 容器（`docker compose stop agent`），api/web 继续提供家谱功能；in-flight Run 由 FastAPI lease reaper 自动回收。

### 健康检查

```bash
docker compose ps                          # 三个服务应为 Up (healthy)
curl -f http://localhost:8000/api/health   # {"status":"ok"}
docker compose exec agent node -e "fetch('http://127.0.0.1:8080/readyz').then(r=>process.exit(r.ok?0:1))"
```

### 优雅停机

- **api**：`stop_grace_period: 30s`。`docker compose stop api` → SIGTERM → uvicorn 停止接收新连接、完成在途请求、释放 Run lease。
- **agent**：`stop_grace_period: 10s`。SIGTERM → `worker.stop()` + `health.close()`；在途 Run 由 FastAPI reaper 按 `lease_expires_at` 回队/判死，下一个 sidecar 实例重新 lease。
- **禁止** `docker compose kill`（SIGKILL）用于正常停机——在途 Run 与 SSE 连接会丢失，仅靠 lease 超时恢复。

### Run lease 恢复

sidecar crash 或网络断开后，`agent_runs` 表中 `leased`/`running` 状态的 Run 由 `reaper` 按 `lease_expires_at`（默认 300s）自动回队或判 `expired` 终态。无需人工干预。断线 SSE 客户端用 `Last-Event-ID` 重连，事件从 DB 重放保证不漏序。

### 日志脱敏

应用日志为 JSON 行格式，字段：`ts/level/logger/msg/user_id/request_id`。**脱敏红线**：PIN、JWT、pin_hash、challenge_token、refresh token、Provider API key 永不入日志；姓名/生卒等 PII 只允许出现在 `audit_log` 表（仅 admin 可读），不进应用日志。

### 事件保留与压缩

- `agent_run_events` 按 Run 单调递增 `seq` 持久化；终态 Run（succeeded/failed/cancelled）的事件保留用于审计与回放。
- 定期压缩建议：对 `settled_at` 超过 90 天的终态 Run，归档事件到冷存储后清理行（未来运维任务实现；当前为 append-only，不自动删除）。
- `audit_log` 保留 ≥ 180 天。
- `web_request_usage` 只存 hash 与标量用量，不存 raw query/payload，可长期保留用于配额与滥用分析。

### Provider secret 轮换

**Agent Provider（LLM）**：

1. 在新 Provider 生成新 API key。
2. 经管理后台 `PATCH /api/admin/agent/providers/{id}` 更新 `secret` 字段（后端用 `secretbox` 加密落库，旧值不可回显）。
3. Agent sidecar 无需重启；ProviderGateway 从后端数据库在下一次 Run 读取新密钥。
4. 旧 key 在 Provider 侧立即吊销；已有 Run 若配置版本发生变化会 fail-closed，由新 Run 使用新配置。

**受控联网 search provider**：

1. 经管理后台 `PUT /api/admin/web/platform` 更新 `provider_secret`（加密落库）。
2. 旧值不可回显；轮换后立即生效，新请求用新 key 解密。

**会话密钥**：`SECRET_KEY` 更换即全部旧 JWT 与 secretbox 密文失效——所有用户需重新登录，已加密的 Provider secret 需重新配置。轮换 `SECRET_KEY` 需在维护窗口进行并通知用户。

### 备份与恢复

见上文「备份与恢复」章节。`verify_restore` 覆盖 V2 真源表（agent/memory/rag/action-card/source-fact/domain-event）并校验 `rag_chunks_fts` 与 active 投影自洽。
