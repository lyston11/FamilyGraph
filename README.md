# FamilyGraph

现代家谱协作 Web 平台：以每个人为第一人称维护家庭空间，家庭空间相连自然涌现家族视图。

系统架构与设计总览见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；开发流程、任务和规范见 `.trellis/` 与根目录 [AGENTS.md](AGENTS.md)。`.agent-notes/` 仅保留早期迁移期间的历史记录。

技术栈：Vue 3 + Vite + TypeScript（前端）｜FastAPI + SQLAlchemy + SQLite(WAL)（后端）｜Docker Compose（部署）。

## 仓库布局

```
backend/                 FastAPI 应用 + Alembic 迁移 + pytest/ruff/mypy 门禁
frontend/                家庭用户 Vue3 + Vite + TS 应用 + eslint/vitest 门禁
system-admin-frontend/   系统管理员独立后台应用（仅访问 /admin-api）
```

## 启动方式一：容器模式（推荐）

```bash
# 正式部署前设置强随机密钥（Agent Runtime 依赖第二个；系统管理员后台依赖第三组）
cat > .env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
AGENT_SERVICE_SECRET=$(openssl rand -hex 32)
ADMIN_JWT_SECRET=$(openssl rand -hex 32)
ADMIN_JWT_ISSUER=familygraph-admin
ADMIN_JWT_AUDIENCE=familygraph-admin-web
EOF
chmod 600 .env

docker compose up --build -d

curl -f http://localhost:8000/api/health   # {"status":"ok"}
curl -f http://localhost:8080/api/health   # 经 nginx 反代，同样返回 ok
# 浏览器打开 http://localhost:8080
```

数据落盘于命名卷 `app_data`（容器内 `/data`：`db/` SQLite 主库+WAL 文件、`uploads/`、`backups/`、`bootstrap/`）。

## 启动方式二：开发模式（本地或远程）

前置要求：Python ≥ 3.12、Node ≥ 22。

选择与环境匹配的一键脚本。远程隧道已安装时，远程模式是默认选择；没有隧道或需要隔离本地数据库时使用全本地模式。两个模式互斥。

```bash
# 远程开发：服务器后端/数据库/dbx，本地前端 + SSH 隧道
./scripts/dev-up-remote.sh

# 全本地开发：三 listener + 两个前端
./scripts/dev-up.sh
```

脚本会跳过已运行进程、写入 `.dev-logs/` 并检查健康端点。dbx 仅在服务器部署，远程模式通过 `http://127.0.0.1:4225` 访问。后端代码在服务器更新后执行 `git pull`、必要时 `alembic upgrade head`，再 `systemctl --user restart familygraph-api`。

只有在故障排查、调试单个进程或运行隔离测试时才手动逐个启动；此时仍需自行配置环境变量并验证对应端口：

```bash
# 终端 1 —— 后端（三个 listener：家庭 8000 / agent 内部 8001 / 管理员 8002）
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export SECRET_KEY=$(openssl rand -hex 32)          # 必需；缺失时应用拒绝启动
export ADMIN_JWT_SECRET=$(openssl rand -hex 32)    # 管理员独立签发域；缺失/过弱拒启
export ADMIN_JWT_ISSUER=familygraph-admin
export ADMIN_JWT_AUDIENCE=familygraph-admin-web
python -m app.serve                            # 8000/8001/8002；健康检查见下文

# 终端 2 —— 家庭前端
cd frontend
npm ci
npm run dev                                 # http://localhost:5173，/api 由 vite 代理到 :8000

# 终端 3 —— 系统管理员后台前端（开发 5174）
cd system-admin-frontend
npm ci
npm run dev                                 # http://localhost:5174，/admin-api 由 vite 代理到 :8002
```

## 数据库迁移（Alembic）

```bash
cd backend
alembic upgrade head          # 从空库应用到最新
alembic downgrade base        # 回滚到空库
alembic revision -m "change"  # 生成新迁移（业务表结构随各子任务迁移引入）
```

数据库 URL 由 `app/config.py` 统一提供（默认 `<cwd>/data/db/app.db`，可用 `DATA_DIR` 覆盖）；启动时自动设置 PRAGMA：`foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL`。

## 按改动范围选择验证

验证命令按受影响包和风险选择，不要求每次改动运行全量门禁。提交或交付前至少完成与改动直接相关的检查，并记录未运行的高成本检查及原因。

| 改动类型 | 建议检查 |
|---|---|
| 文档、配置说明、脚本注释 | Markdown/格式检查；涉及 Trellis 任务或规范时运行 `python3 ./.trellis/scripts/task.py validate <task-dir>` |
| 后端单包或纯函数 | `cd backend && ruff check <files> && .venv/bin/python -m pytest -q <相关测试>`；改公共类型时加 `mypy app` |
| 家庭前端或后台前端 | 在对应目录运行 `npm run lint`、`npm run type-check`，必要时 `npm test`；构建/发布改动再运行 `npm run build` |
| 数据模型、迁移、认证、权限、跨 listener 或跨前后端 | 运行受影响包的完整检查，并执行相关回归测试；涉及真实 HTTP 契约时运行 API smoke |
| 发布、部署、依赖升级或无法确定影响面 | 三个包的完整检查，必要时再运行 API smoke |

低风险、可逆改动可以只做针对性检查；测试失败、环境缺失或 smoke 返回 blocked 必须报告，不得标记为通过。

### 常用完整检查

```bash
cd backend                 && ruff check . && ruff format --check . && mypy app && pytest
cd frontend                && npm run lint && npm run type-check && npm test && npm run build
cd system-admin-frontend   && npm run lint && npm run type-check && npm test && npm run build
```

### 真实 API smoke（按需）

当改动涉及 API 路由、认证授权、跨 listener 边界、数据库迁移、前后端契约或发布流程时，在隔离环境启动真实后端三 listener 并执行端到端契约用例：

```bash
./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json
```

- 随机端口 + 一次性临时 `DATA_DIR`（合成 seed），不触碰业务库与用户 `.env`；
  运行结束自动清理临时目录与 bootstrap 凭据；
- 退出码：`0` 全部通过 / `1` 有真实失败 / `2` 环境阻塞（如 backend/.venv 缺失、
  alembic 失败、listener 60s 未就绪）——**blocked 不等于通过**，不得写进发布证据；
- 报告为脱敏 JSON：只含用例 ID、listener、method/path 模板、状态码、错误码、
  耗时；不含姓名、PIN、JWT、Cookie、prompt、provider 响应或本地路径。

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

通过 `/api/admin/agent/providers` 提交上述非敏感字段，并在创建请求的 `secret` 字段注入 API key。密钥只会以 secretbox 密文存入后端，响应只返回 `has_secret`；不要把 key 写入仓库、日志、Agent Notes 或 Agent 容器环境。随后用 `/api/admin/agent/spaces/{space_id}/provider-settings` 选择 `gpt-5.6-sol` 并设置 `cloud_allowed=true`。

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
2. `git clone` 本仓库 → 配置 `.env`：`SECRET_KEY=<openssl rand -hex 32>`、`AGENT_SERVICE_SECRET=<openssl rand -hex 32>`、`ADMIN_JWT_SECRET=<openssl rand -hex 32>`、`ADMIN_JWT_ISSUER=familygraph-admin`、`ADMIN_JWT_AUDIENCE=familygraph-admin-web`、`DATA_DIR=/data`，并执行 `chmod 600 .env`。
3. `docker compose up --build -d` → 服务自动初始化（家庭账号由开通流程创建）。
4. 数据迁移：本机执行备份 → 把 tar 包传服务器 → 按上文恢复流程导入数据卷 → 重启。
5. 域名：DNS A 记录指向服务器 IP；HTTPS 二选一：
   - 方案 A（推荐）：Caddy 反代 80/443，自动签发 Let's Encrypt；
   - 方案 B：certbot + nginx 手动配置证书。
6. 定期备份建议：crontab 每日执行备份命令，并把 `/data/backups` 同步到对象存储。

---

## 系统管理员后台（仅运维可见，勿写入面向用户的文档）

后台是独立前端（`system-admin-frontend/`）+ 独立 API listener（api 容器内 8002，`/admin-api/*`），与家庭端完全隔离：独立 JWT 签发域（`ADMIN_JWT_*`）、独立浏览器存储、admin Docker 网络内互连；**8002 不发布宿主端口**，唯一入口是 admin-web。

```bash
# 访问（生产仅回环，由 VPN/内网反代进入）
curl -f http://127.0.0.1:8081/            # 后台前端
curl -f http://127.0.0.1:8081/admin-api/health

# 首个管理员凭据（部署启动自动生成，唯一用户名 admin）
docker compose exec api ls -la /data/bootstrap/
docker compose exec api cat /data/bootstrap/admin-credentials   # 仅此一次；首登强制改密
```

- 凭据文件权限 `0600`；**首次成功改密后自动删除**，数据库不存明文，日志不打印。
- 忘记密码（受限运维恢复）：

```bash
docker compose exec api python -m app.admin_recovery
# 生成一次性恢复密码（只落 0600 文件 /data/bootstrap/admin-recovery），
# 旧 refresh 会话全部撤销，首登强制改密
```

- 路由矩阵自检：宿主直连 `127.0.0.1:8002` 必须失败；家庭 web `http://localhost:8080/admin-api/health` 返回普通 404。
- 后台业务面是全业务只读监控 + 空间管理员申请审批（唯一写例外）；敏感详情需提交理由创建 30 分钟访问会话，全部读取留痕（`admin_access_audits` 永久保留）。

---

## 运维手册（V2.6 发布治理）

### 功能开关与 kill switch

所有 V2 功能默认关闭，可通过环境变量逐层启用（compose `.env` 或 `docker compose` 的 `environment` 段）：

| 功能 | 环境变量 | 默认 | 说明 |
|------|----------|------|------|
| Agent Runtime | `AGENT_RUNTIME_ENABLED` | `0` | 关闭时 `/internal/agent/*` 一律 503 |
| 关系智能 | `RELATIONSHIP_INTELLIGENCE_ENABLED` | `0` | 关闭时关系解析端点 503 |
| Steward | `STEWARD_ENABLED` | `0` | 关闭时 ActionCard 入口 503；admin listener `GET /admin-api/v1/steward/status` 始终可读 disabled 状态 |
| Steward worker/扫描 | `STEWARD_WORKER_ENABLED` / `STEWARD_SCAN_INTERVAL_SECONDS` 等 | `0` / `300` | worker 关闭时作业可排队不执行（paused）；扫描间隔/退避/重跑冷却见 `backend/app/config.py`（正数 + 上界校验） |
| Steward 积压告警 | `STEWARD_ALERT_QUEUE_SECONDS` | `0` | 最老 queued 作业年龄超过该阈值（0=自动=max(2×扫描间隔, 60s)）且核心已启用时，`GET /admin-api/v1/steward/status` 的 `alerts` 输出 `queue_backlog`；worker 启用但连续两个扫描窗口无结算进展输出 `queue_stalled` 并把 state 升级 `degraded`。仅产生可见告警，不改变调度行为 |
| Memory/RAG | `MEMORY_ENABLED` / `RAG_ENABLED` | `0` | 关闭时无候选/检索，工具路径保留 |
| 受控联网 | `CONTROLLED_WEB_ENABLED` | `0` | 平台总开关；空间还需 owner/admin 单独 opt-in |

**紧急 kill switch**：任何 Web 安全问题先全局关闭——设 `CONTROLLED_WEB_ENABLED=0` 重启 api，或经平台运营 API（platform_operator 专属）把平台配置 `enabled` 置 false。移除工具披露即可，不影响本地 Assistant/Steward 和 v1 家谱功能。

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
2. 经平台运营 API（platform_operator 专属）`PATCH /api/admin/agent/providers/{id}` 更新 `secret` 字段（后端用 `secretbox` 加密落库，旧值不可回显）。
3. Agent sidecar 无需重启；ProviderGateway 从后端数据库在下一次 Run 读取新密钥。
4. 旧 key 在 Provider 侧立即吊销；已有 Run 若配置版本发生变化会 fail-closed，由新 Run 使用新配置。

**受控联网 search provider**：

1. 经平台运营 API（platform_operator 专属）`PUT /api/admin/web/platform` 更新 `provider_secret`（加密落库）。
2. 旧值不可回显；轮换后立即生效，新请求用新 key 解密。

**会话密钥**：`SECRET_KEY` 更换即全部旧 JWT 与 secretbox 密文失效——所有用户需重新登录，已加密的 Provider secret 需重新配置。轮换 `SECRET_KEY` 需在维护窗口进行并通知用户。

### 备份与恢复

见上文「备份与恢复」章节。`verify_restore` 覆盖 V2 真源表（agent/memory/rag/action-card/source-fact/domain-event）并校验 `rag_chunks_fts` 与 active 投影自洽。
