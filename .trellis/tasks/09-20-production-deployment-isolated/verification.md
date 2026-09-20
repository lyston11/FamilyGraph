# 验收记录：线上环境部署（fg.lyston.qzz.io）

状态：**全部通过**。验收时间 2026-09-20；线上代码 = `022c3fd`。

## 交付物

| 文件 | 作用 |
|---|---|
| `deploy/production/docker-compose.prod.yml` | 线上叠加配置（项目名、端口反转、镜像标签） |
| `deploy/production/familygraph-prod.env.example` | 线上环境变量模板（无真实密钥） |
| `deploy/production/README.md` | 运维手册（拓扑、隔离矩阵、发布、备份、回滚、排障） |
| `scripts/install-prod-automation.sh` | 幂等安装器（前置校验 + 构建 + 启动 + 备份定时器） |
| `scripts/prod-backup.sh` | 线上备份（走 `app.backup`，online backup API） |
| `.dockerignore`（仓库根） | 收窄以仓库根为上下文时的构建传输量 |
| `docker-compose.yml` 修补 | 透传 5 个被静默丢弃的 env 键；admin-web 构建上下文改仓库根 |
| `system-admin-frontend/Dockerfile` | 适配仓库根上下文（既有 bug，容器模式从未构建成功） |

## 部署完成态

```
familygraph-prod-admin-web-1  healthy  127.0.0.1:8101->80/tcp
familygraph-prod-agent-1      healthy  8080/tcp（无宿主发布）
familygraph-prod-api-1        healthy  8000/tcp（无宿主发布）
familygraph-prod-web-1        healthy  127.0.0.1:8100->80/tcp
卷：familygraph-prod_app_data（CreatedAt 2026-09-20T06:25:10Z）
网络：familygraph-prod_{frontend,backend(internal),admin}
备份定时器：familygraph-prod-backup.timer（enabled，每 6 小时）
```

## 逐条验收证据

### AC1 公网家庭端可达 ✅

```
https://fg.lyston.qzz.io/             → 200（SPA，<title>FamilyGraph</title>）
https://fg.lyston.qzz.io/api/health   → 200
TLS: subject CN=lyston.qzz.io, issuer Let's Encrypt, verify ok
```

### AC2 数据隔离 ✅

```
卷宿主路径 : /var/lib/docker/volumes/familygraph-prod_app_data/_data
开发库路径 : /home/ubuntu/projects/FamilyGraph/backend/data/db/app.db
inode 比对 : dev=2049:5018940  prod=2049:5565300  → DIFFERENT
开发库大小 : size=34787328（与部署前基线一致）
开发库行数 : users=51（与部署前基线一致）
```

说明：开发库 `mtime` 会在部署前后变化，这是**开发环境自身**在持续写库
（`STEWARD_WORKER_ENABLED=1` + `MAINTENANCE_INTERVAL_SECONDS=5` 常驻），
与本任务无关。因此 AC2 的判定口径是**路径/inode + 大小 + 行数**，不是 mtime。

### AC3 迁移版本与演示成员 ✅

```
alembic_version = 0053_member_approval_and_labels（= head）
users=51  family_spaces=20  relations=74
朱元璋 / 马皇后 / 徐达 均在；朱元璋 pin_must_change=0
```

### AC4 演示账号公网登录 ✅

```
POST https://fg.lyston.qzz.io/api/auth/login {"name":"朱元璋","pin":"123456"}
→ 200，返回 access_token（len 284），user.name = 朱元璋
→ GET /api/me 用该 token → 200（无需改密）
```

### AC5 认证域隔离 ✅

```
SECRET_KEY            dev ≠ prod
AGENT_SERVICE_SECRET  dev ≠ prod
ADMIN_JWT_SECRET      dev ≠ prod

开发 token → 线上 /api/me : 401
线上 token → 开发 /api/me : 401
线上 token → 线上 /api/me : 200
开发 token → 开发 /api/me : 200
```

### AC6 管理后台仅内网 ✅

```
https://fg.lyston.qzz.io/admin-api/health      → 404（普通 404，无后台痕迹）
https://fg.lyston.qzz.io/admin-api/auth/login  → 404（POST 同样 404）
8100 绑定 : 127.0.0.1:8100（字段级断言，仅回环）
8101 绑定 : 127.0.0.1:8101（字段级断言，仅回环）
公网 DNS  : admin./fg-admin./admin-fg.lyston.qzz.io 均无 A 记录
容器内    : ADMIN_API_HOST=172.29.0.10、INTERNAL_AGENT_API_HOST=172.28.0.10
内网可达  : http://127.0.0.1:8101/ → 200、/admin-api/health → 200（仅供 SSH 隧道）
```

### AC7 进程隔离 ✅

```
docker compose -p familygraph-prod stop
  开发 /api/health      → 200
  开发 /admin-api/health→ 200
  开发 agent systemd    → active
  https://fg.lyston.qzz.io/api/health → 502（符合预期）
docker compose -p familygraph-prod start
  https://fg.lyston.qzz.io/api/health → 200（恢复）
```

### AC8 端口与 agent 隔离 ✅

```
docker port 结果：
  familygraph-prod-api-1        （空）
  familygraph-prod-agent-1      （空）
  familygraph-prod-web-1        80/tcp -> 127.0.0.1:8100
  familygraph-prod-admin-web-1  80/tcp -> 127.0.0.1:8101

开发端口归属未变：8000/8001/8002（python pid 2060034）、18080（node pid 1519323）
```

### AC9 真实模型调用 ✅

```
POST /api/agent/sessions {space_id: 2} → session 1
POST /api/agent/sessions/1/messages  → run 1 status=queued
45s 后 run 1 status = succeeded, attempt = 1
assistant 回复 = "我是 FamilyGraph 的只读家谱助手，只根据当前空间中你可见的
                  家谱资料，帮你查询和理解人物关系。"

audit_log 中 agent_provider_egress：
  {"provider_id": 1, "status": "succeeded", "upstream_status": 200,
   "bytes_read": 35281, "header_ms": 3018}
```

Provider 密钥跨环境迁移：开发密文用开发 `SECRET_KEY` 解密 → 线上 `SECRET_KEY`
重加密写回；明文只在进程内存（经 stdin 传递，不进 argv/env/日志/文件）。
迁移后解密验证：`key length = 67`，`provider_profile_error` 为 `None`。
迁移脚本一次性使用，已从宿主与容器内删除，未提交进仓库。

### AC10 定期备份 ✅

```
familygraph-prod-backup.timer : enabled，next 约 6 小时后
手工触发 unit → Result=success, ExecMainStatus=0
产物：/data/backups/familygraph-20260920-063115.db（8626176 B）+ .tar.gz
      /data/backups/familygraph-20260920-063208.db（8626176 B）+ .tar.gz
PRAGMA integrity_check → ok（两份快照均通过）
users in snapshot      → 51
备份内行数含 agent_sessions=1 / agent_runs=1（含本次 smoke 的真实运行）
```

排查记录：unit 首次执行失败，根因是 systemd **user manager** 启动于
2026-07-27（早于用户加入 `docker` 组），其派生单元环境不含 docker 组，
`docker` CLI 报 `permission denied`。已在 `prod-backup.sh` 中加
`sg docker` 单次重执行（`FG_PROD_BACKUP_REEXEC` 防环），手工运行不受影响。

### AC11 幂等 ✅

```
重跑 scripts/install-prod-automation.sh → exit=0
重跑前后完全一致：
  users=51  admins=1  providers=1  defaults=1  space_settings=3  action_cards=19
  卷 CreatedAt = 2026-09-20T06:25:10Z（未重建）
  /data/bootstrap/admin-credentials 仍在（未被重启重置）
  Provider 密钥仍可解密（key length = 67）
```

排查记录：首次重跑失败于端口预检把**本栈自己的** 8100/8101 当成外部占用。
已修正为用 `docker ps --filter publish=<port>` 区分自身容器与外部占用者。

## 副作用与兼容性核对

- 宿主 nginx **未改动**：`sites-enabled/` 仍是 `default`、
  `lyston.qzz.io.conf`、`note.lyston.qzz.io.conf`（隧道路由直连 8100，
  不经宿主 nginx）。
- 相邻站点未受影响：`lyston.qzz.io`（1Panel）200、`1panel.lyston.qzz.io` 200、
  `note.lyston.qzz.io` 302。
- 开发环境零改动：其 systemd 单元、env 文件（mtime 2026-09-17 04:52）、
  代码目录、数据库大小与行数均未变化。
- 无新增 Alembic 迁移 → 无迁移序号冲突。
- 未改动 backend / frontend / agent 源码（只改 compose/Dockerfile/新增部署文件）。

## 修掉的两个既有缺陷（非本任务引入，但阻塞部署）

1. **admin 镜像在容器模式从未构建成功**：`1bc95ca`（09-11）引入
   `shared/brand-tokens.css` 并由 `system-admin-frontend/src/styles/main.css`
   以 `@import '../../../shared/brand-tokens.css'` 消费，但没同步 Docker 构建
   上下文（仍是 `system-admin-frontend/`），该文件落在上下文之外 →
   `vite build` ENOENT。开发环境是裸机（不构建镜像），所以一直没暴露。
   修复：上下文改仓库根 + 把 token 拷到对应相对深度 + 新增根 `.dockerignore`。
2. **compose 静默丢弃 5 个 env 键**：api 的 `environment` 是显式白名单，
   `ADMIN_INITIAL_PASSWORD` / `MEMORY_ENABLED` / `RAG_ENABLED` /
   `STEWARD_ASSIST_TERMINOLOGY` / `STEWARD_ASSIST_TIMEOUT_SECONDS` 未透传，
   在 `.env` 里写了也不生效。已按后端默认值补上。

## 未运行的高成本检查及原因

- **未跑三包完整检查套件**（`ruff`/`mypy`/`pytest`、`npm lint`/`type-check`/`test`/`build`）：
  本任务不改 backend/frontend/agent 源码，只改 compose、Dockerfile 与新增
  部署脚本；受影响面由「服务器上 `docker compose config` 解析校验 + 镜像真实
  构建 + 部署后真实健康/隔离/模型调用验收」覆盖，强于静态检查。
- **未跑 `scripts/frontend-api-smoke.sh`**：该脚本启动**隔离 DATA_DIR 的本地三
  listener**，与本次线上部署无交集；线上验收已用真实公网端点完成。
- **未跑浏览器端 Playwright 验收**：本轮验收以 HTTP 层面的端到端证据为准
  （公网登录、`/api/me` 200、assistant run succeeded、egress 审计）。若需
  补浏览器层证据，建议作为独立小任务。
- **未验证 CI/CD**：线上发布是显式手工动作（`git checkout <sha>` + 安装脚本），
  本任务有意不引入自动发布流水线。

## 回滚

见 `deploy/production/README.md` 的三级回滚表。一级回滚
（`docker compose -p familygraph-prod stop`）已验证：开发环境不受影响，
`fg.lyston.qzz.io` 回到 502（部署前状态）。
