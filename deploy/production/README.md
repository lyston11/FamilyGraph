# FamilyGraph 线上环境（`fg.lyston.qzz.io`）

本目录承载线上环境的部署差异，与开发环境（服务器上的裸机 systemd 用户单元 +
`~/projects/FamilyGraph`）**完全隔离**。需求与验收见
`.trellis/tasks/09-20-production-deployment-isolated/prd.md`。

## 拓扑

```
Internet ──HTTPS──► Cloudflare 边缘（zone: lyston.qzz.io）
                        │ fg 的 Public Hostname → http://127.0.0.1:8100
                        ▼
                  cloudflared（宿主，token 模式）
                        │
                  127.0.0.1:8100
                        ▼
   ┌──────────── Docker Compose 项目 familygraph-prod ────────────┐
   │  web(:80→8100) ──► api(:8000) ──► 线上数据卷 /data            │
   │                      ▲  ▲                                    │
   │                      │  └── agent（内部协议 8001，无宿主端口）│
   │  admin-web(:80→127.0.0.1:8101) ──► api(:8002，仅 admin 网络)  │
   └──────────────────────────────────────────────────────────────┘
```

关键点：**隧道的 Service 直连 `127.0.0.1:8100`，不经过宿主 nginx**。因此
本环境不需要在 `/etc/nginx/sites-enabled/` 新增站点，也不影响 1Panel
（`lyston.qzz.io` / `1panel.lyston.qzz.io`）与 note 等既有站点。

## 隔离矩阵

| 维度 | 开发环境 | 线上环境 |
|---|---|---|
| 代码 | `/home/ubuntu/projects/FamilyGraph` | `/home/ubuntu/fg-prod`（独立 clone） |
| 自动同步 | `familygraph-code-sync.timer`（30 min） | 无，发布是显式动作 |
| 数据库 | `…/projects/FamilyGraph/backend/data/db/app.db` | 卷 `familygraph-prod_app_data` 内 `/data/db/app.db` |
| 进程 | systemd 用户单元 `familygraph-api` / `familygraph-agent` | 容器 `familygraph-prod-{api,web,admin-web,agent}` |
| 宿主端口 | 8000 / 8001 / 8002 / 18080 | 8100（家庭 web）、8101（管理 web，仅回环） |
| 密钥 | `~/.config/familygraph/familygraph.env` | `~/.config/familygraph/familygraph-prod.env` |
| 公网入口 | 无（SSH 隧道） | `https://fg.lyston.qzz.io`（仅家庭端） |

`SECRET_KEY` 不同 ⇒ 两侧 JWT 互不承认；Provider 密钥密文用 `SECRET_KEY`
派生密钥加密 ⇒ 必须跨环境重加密（见下）。

## 首次部署

```bash
# 1) 独立 clone（不要复用开发 checkout）
git clone git@github.com:lyston11/FamilyGraph.git /home/ubuntu/fg-prod

# 2) 生成线上环境文件（三个密钥必须是全新随机值）
cp /home/ubuntu/fg-prod/deploy/production/familygraph-prod.env.example \
   ~/.config/familygraph/familygraph-prod.env
# 编辑填入 SECRET_KEY / AGENT_SERVICE_SECRET / ADMIN_JWT_SECRET
#   每个都用：openssl rand -hex 32
chmod 600 ~/.config/familygraph/familygraph-prod.env

# 3) 一键安装（幂等；含构建、启动、备份定时器）
bash /home/ubuntu/fg-prod/scripts/install-prod-automation.sh
```

安装脚本会在启动前拒绝：密钥与开发环境相同、`ENV_FILE` 权限不是 600、
宿主 8100/8101 被占用。这些是隔离的硬前提，不满足时不会留下半成品状态。

### Cloudflare 侧（一次性，手工）

在 Zero Trust 控制台 → **Networks → Tunnels** → 找到 tunnel
`69504be9-a5e1-4f84-8374-638888e8496e` → **Public Hostname** → **Add**：

| 字段 | 值 |
|---|---|
| Subdomain | `fg` |
| Domain | `lyston.qzz.io` |
| Path | 留空 |
| Service Type | `HTTP` |
| Service URL | `127.0.0.1:8100` |

保存后 Cloudflare 会自动在 `lyston.qzz.io` zone 建一条指向
`69504be9-….cfargotunnel.com` 的代理 CNAME。

**不要**为系统管理员后台添加任何 Public Hostname。

## 日常操作

```bash
# 状态
docker compose -p familygraph-prod --env-file ~/.config/familygraph/familygraph-prod.env \
  -f /home/ubuntu/fg-prod/docker-compose.yml \
  -f /home/ubuntu/fg-prod/deploy/production/docker-compose.prod.yml ps

# 日志
docker compose -p familygraph-prod logs -f api
docker compose -p familygraph-prod logs -f agent    # assistant 停 queued 先看这里

# 停止 / 启动（不影响开发环境）
docker compose -p familygraph-prod stop
docker compose -p familygraph-prod start
```

### 系统管理员后台（仅内网）

```bash
ssh -N -L 8101:127.0.0.1:8101 lyston
# 浏览器打开 http://127.0.0.1:8101
```

初始凭据：

```bash
docker compose -p familygraph-prod exec api cat /data/bootstrap/admin-credentials
```

首登强制改密后该文件自动删除。忘记密码时用容器内恢复命令
（`python -m app.admin_recovery`）。

## 发布流程

线上**不启用**自动代码同步，发布是显式动作：

```bash
cd /home/ubuntu/fg-prod
git fetch origin main
git checkout <目标 commit sha>       # 或 git merge --ff-only origin/main
bash scripts/install-prod-automation.sh   # 重建镜像 + 滚动更新 + 校验
```

镜像内容变了才会重新构建；`docker compose build` 有层缓存，通常很快。
若本次发布含 Alembic 迁移，`api` 容器的启动命令会先跑
`alembic upgrade head` 再服务，无需单独执行迁移。

**部署顺序硬约束**（见记忆中的增量事件契约）：若发布涉及
`assistant.text_delta` / `assistant.text_reset` 这类新增事件类型，
必须**后端先于 agent sidecar** 上线；回退顺序相反。本环境 `api` 与
`agent` 在同一 compose 项目内，`compose up -d` 的顺序由依赖关系决定，
但跨版本升级时仍应确认 `api` 先 healthy 再让 `agent` 接新协议。

## 备份

`familygraph-prod-backup.timer` 每 6 小时（`Persistent=true`）调用
`scripts/prod-backup.sh`，经 `compose exec api python -m app.backup` 产出：

- `/data/backups/familygraph-<时间戳>.db`（SQLite online backup API 快照 + `integrity_check` 自检）
- `/data/backups/familygraph-<时间戳>.tar.gz`（含 uploads）

保留 30 天。**禁止**运行期直接 `cp` 主库（WAL 模式下会得到不一致快照）。

手工触发与核验：

```bash
systemctl --user start familygraph-prod-backup.service
docker compose -p familygraph-prod exec -T api sh -c \
  'ls -la /data/backups/ | tail -5'
docker compose -p familygraph-prod exec -T api python -c \
  'import sqlite3,glob; f=sorted(glob.glob("/data/backups/familygraph-*.db"))[-1];
   print(f, sqlite3.connect(f).execute("PRAGMA integrity_check").fetchone()[0])'
```

## Provider 密钥跨环境迁移

`agent_providers.secret_ciphertext` 由 `SECRET_KEY` 派生密钥加密
（`backend/app/utils/secretbox.py`），线上 `SECRET_KEY` 不同 ⇒ 密文不能直接
拷贝。两条路径：

**路径 A（推荐，一次性脚本）**：在线上容器内用旧 `SECRET_KEY` 解密、用新
`SECRET_KEY` 重加密写回。明文只存在于进程内存，不落盘、不打印、不进日志。
该脚本含密钥操作，**不提交进仓库**。

**路径 B（回退，纯手工）**：在管理后台（`http://127.0.0.1:8101`）的 Agent
Provider 治理页重新注册 `liu-dada` / `gpt-5.6-sol` / `openai-responses` /
`https://api.liu-dada.com/v1` 并粘贴 API key。之后在空间模型设置里为
`assistant` 与 `steward` 各选一次 Provider。

两条路径都完成后，线上库还需要 `agent_platform_defaults` 与
`agent_space_provider_settings` 行与开发同构，否则 assistant 会因
`PROVIDER_UNRESOLVED` 停在 denied。

## 隔离验收命令

```bash
# 数据：线上库是卷内文件，开发库路径与内容未变
docker volume ls | grep familygraph-prod
stat -c '%s %y' ~/projects/FamilyGraph/backend/data/db/app.db

# 密钥：两侧不同
diff <(grep ^SECRET_KEY= ~/.config/familygraph/familygraph.env) \
     <(grep ^SECRET_KEY= ~/.config/familygraph/familygraph-prod.env) \
  && echo 'SAME (FAIL)' || echo 'DIFFERENT (OK)'

# 端口：线上只有 8100/8101，且都绑回环
ss -tlnp | grep -E ':8100|:8101'

# 进程：停线上不影响开发
docker compose -p familygraph-prod stop
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/health   # 期望 200
docker compose -p familygraph-prod start

# 后台隔离：公网无后台痕迹，8101 仅回环
curl -s -o /dev/null -w '%{http_code}\n' https://fg.lyston.qzz.io/admin-api/health  # 期望 404
ss -tlnp | grep ':8101' | grep -v '127.0.0.1:8101' && echo 'LEAK (FAIL)' || echo 'loopback only (OK)'
```

## 回滚

| 级别 | 动作 | 影响 |
|---|---|---|
| 一级（保留数据） | `docker compose -p familygraph-prod stop` | 公网 `fg` 返回 502（与部署前一致）；开发环境不受影响 |
| 二级（删栈保卷） | `... down`（不加 `-v`） | 数据保留在卷内，可再次 `up` 恢复 |
| 三级（彻底移除） | `... down -v` + `rm -rf /home/ubuntu/fg-prod` + 删 `~/.config/familygraph/familygraph-prod.env` + `systemctl --user disable --now familygraph-prod-backup.timer` + 删 Cloudflare 的 `fg` Public Hostname | 线上数据不可恢复（先确认备份） |

开发环境在本任务中**没有任何改动**，无需回滚。

## 排障

| 现象 | 排查 |
|---|---|
| `https://fg.lyston.qzz.io` 返回 502 | `ss -tlnp \| grep :8100` 是否监听；`docker compose -p familygraph-prod ps` 是否 healthy |
| 返回 404 且是 nginx 页面 | 隧道 Service 被指到了 `localhost:80`（宿主 nginx）。改成 `127.0.0.1:8100` |
| 助手发消息无回复 | `docker compose -p familygraph-prod logs agent`；容器不在则 run 永远 `queued` |
| 助手报 `PROVIDER_UNRESOLVED` | Provider 密钥未迁移，或 `agent_space_provider_settings` 缺行（见上） |
| `docker compose up` 报端口被占用 | 8100/8101 被其他进程抢走；脚本启动前会 fail-fast 提示 |
| 演示账号登录要求改密 | 线上库不是空库（被复用）。检查 `docker volume ls` 是否误用了开发卷 |
| 备份定时器未触发 | `systemctl --user list-timers 'familygraph-prod-*'`；未启用 linger 时注销后停止 |
