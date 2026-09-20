# 技术设计：线上环境部署（fg.lyston.qzz.io）

## 1. 边界与拓扑

```
                       Internet
                          │  HTTPS (CF 边缘 TLS)
                          ▼
              Cloudflare (zone: lyston.qzz.io)
                  fg  → CNAME → 69504be9-….cfargotunnel.com
                          │
                    cloudflared (host, token mode)
                          │
                          │  按 Public Hostname 的 Service 直接回源
                          ▼
                127.0.0.1:8100            (host loopback)
                          │
   ┌──────────────────────┴───────────────────────────────────┐
   │ Docker Compose project: familygraph-prod                 │
   │                                                          │
   │  web (nginx) ──frontend net──► api ──backend net──► agent│
   │   :80→8100                     :8000 / :8001 / :8002     │
   │                                  ▲                       │
   │  admin-web (nginx) ──admin net───┘                       │
   │   :80→127.0.0.1:8101                                     │
   │                                                          │
   │  volume: familygraph-prod_app_data → /data               │
   └──────────────────────────────────────────────────────────┘

   SSH 隧道（仅管理员）
   本机 127.0.0.1:8101 ──ssh -L──► host 127.0.0.1:8101
```

关键点：`cloudflared` 的 Public Hostname `fg.lyston.qzz.io` 的 Service 是
`http://127.0.0.1:8100`，**绕过宿主 nginx**，直连线上 web 容器映射端口。
因此本任务**不需要**新增 `/etc/nginx/sites-enabled/fg.lyston.qzz.io.conf`，
也**不触碰**宿主 nginx（避免影响 1Panel / note 等既有站点）。

## 2. 隔离矩阵

| 维度 | 开发环境（现状，不改） | 线上环境（新增） |
|---|---|---|
| 代码目录 | `/home/ubuntu/projects/FamilyGraph` | `/home/ubuntu/fg-prod` |
| Git 来源 | 主 checkout（`main`） | 独立 `clone`，发布时显式 `fetch` + `checkout <sha>` |
| 自动同步 | `familygraph-code-sync.timer`（30 min） | **无**（显式发布） |
| DB 文件 | `…/projects/FamilyGraph/backend/data/db/app.db` | named volume `familygraph-prod_app_data` 内 `/data/db/app.db` |
| 卷 | 无（裸机直写目录） | `familygraph-prod_app_data` |
| 进程 | systemd user：`familygraph-api` / `familygraph-agent` | docker compose：`familygraph-prod-api` / `-web` / `-admin-web` / `-agent` |
| 宿主端口 | 8000 / 8001 / 8002 / 18080 | 8100（家庭 web）、8101（管理 web，仅回环）；api/agent 不发布 |
| Docker 网段 | 无 | `172.28.0.0/24`（backend，internal）、`172.29.0.0/24`（admin） |
| SECRET_KEY | `~/.config/familygraph/familygraph.env` | `~/.config/familygraph/familygraph-prod.env`（新值） |
| AGENT_SERVICE_SECRET | 同上 | 新值 |
| ADMIN_JWT_SECRET | 同上 | 新值 |
| 镜像标签 | 无（裸机 venv/node） | `familygraph-prod-api:0.1.0` 等独立标签 |
| 公网 hostname | 无（仅 SSH 隧道） | `fg.lyston.qzz.io`（仅家庭端） |

隔离保证的推导：

- **数据**：不同文件系统路径（宿主目录 vs docker 卷）→ 天然隔离。
- **认证**：`SECRET_KEY` 不同 ⇒ JWT HMAC 签名密钥不同 ⇒ 交叉验签必然失败；
  Provider 密钥密文用 `SECRET_KEY` 派生密钥加密 ⇒ 必须跨环境重加密。
- **端口**：8100/8101 与 8000-8002/18080 不相交；线上 api/agent 无宿主映射。
- **进程**：容器 vs systemd 用户单元，互不感知。
- **代码**：独立 clone + 独立 tag，发布互不影响。

## 3. 文件清单

### 3.1 新增：`deploy/production/docker-compose.prod.yml`

叠加文件（与仓库根 `docker-compose.yml` 组合使用），只表达线上差异：

- `name: familygraph-prod`（固定项目名，避免目录名推导导致卷/网络前缀漂移）
- `api`：`ports: !override []`（撤掉 `8000:8000` 宿主发布）
- `web`：`ports: !override ["127.0.0.1:8100:80"]`
- `admin-web`：`ports: !override ["127.0.0.1:8101:80"]`
- 所有服务镜像加 `-prod` 后缀标签，避免与开发镜像混用

已验证：Docker Compose v5.1.4 支持 `!override`，且 `!override []` 能正确清空
继承的 `ports`（实测见实现记录）。

### 3.2 新增：`deploy/production/familygraph-prod.env.example`

只含键名与占位说明的模板（**不含任何真实密钥值**），用于重装时重建
`~/.config/familygraph/familygraph-prod.env`。真实文件由安装脚本在服务器上
用 `openssl rand -hex 32` 生成，权限 0600。

### 3.3 新增：`scripts/install-prod-automation.sh`

幂等的线上安装器，在服务器上执行，步骤：

1. 前置校验：docker + compose 可用；`familygraph-prod.env` 存在（缺失则
   用模板生成并打印需人工确认的提示）；仓库根 `docker-compose.yml` 存在。
2. `docker compose -p familygraph-prod -f docker-compose.yml -f deploy/production/docker-compose.prod.yml build`
3. `up -d`，等待 `api` healthy
4. 写入并启用 systemd **user** 单元：
   - `familygraph-prod-backup.service` / `.timer`（每 6 小时，`Persistent=true`）
5. 打印访问方式（家庭端 URL、管理端 SSH 隧道命令）与健康检查结果

备份单元通过 `docker compose exec api python -m app.backup` 调用既有
`app.backup`（online backup API + integrity_check），快照落在线上的
`/data/backups` 卷内；保留窗口由 `find -mtime +30 -delete` 实现。

### 3.4 新增：`deploy/production/README.md`

运维手册：日常操作、发布流程、排障口径、回滚步骤、隔离验证命令。

### 3.5 新增：`.trellis/tasks/09-20-production-deployment-isolated/`

`prd.md`（已写）、`design.md`（本文件）、`implement.md`、`implement.jsonl`、
`check.jsonl`。

## 4. Provider 密钥跨环境迁移

`agent_providers.secret_ciphertext` 由 `secretbox.encrypt_secret` 用
`SECRET_KEY` 派生密钥加密（`sha256("familygraph-secretbox:enc:" + SECRET_KEY)`）。
线上 `SECRET_KEY` 不同 ⇒ 直接拷贝密文不可用。

迁移方案（**明文不出服务器进程内存**）：

```bash
# 在线上容器内执行；两侧 SECRET_KEY 通过环境变量注入，明文不落盘不打印
docker compose -p familygraph-prod exec -T api python - <<'PY'
# 从宿主侧只读挂载的开发 env 读取旧 SECRET_KEY，用线上 SECRET_KEY 重加密
PY
```

实现细节：脚本读取 `agent_providers` 行的 `secret_ciphertext`，用**旧**
`SECRET_KEY` 调 `decrypt_secret`，再用**新** `SECRET_KEY` 调 `encrypt_secret`
写回。全程：
- 明文只存在于进程内存与局部变量；
- 不 `print`、不写文件、不进日志（`logging` 完全不涉及）；
- 旧/新 `SECRET_KEY` 通过 `os.environ` 注入单次命令（不进 shell history 的
  做法：用 `env` 前缀 + 变量在脚本内部读取，脚本本身不含密钥值）。

该脚本一次性使用，**不提交进仓库**（含密钥操作，避免误用与泄漏面）。
替代方案（更简单但需人工）：在线上管理后台重新注册 Provider 并手工粘贴
API key。作为**回退路径**记录在运维手册。

## 5. 认证域隔离的验证方法

```
开发 api: http://127.0.0.1:8000（经 SSH 隧道到服务器开发实例）
线上 api: https://fg.lyston.qzz.io

# 取开发 token
T_DEV=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
        -H 'Content-Type: application/json' \
        -d '{"username":"朱元璋","pin":"123456"}' | jq -r .access_token)

# 用开发 token 打线上 → 必须 401
curl -s -o /dev/null -w '%{http_code}\n' https://fg.lyston.qzz.io/api/users/me \
     -H "Authorization: Bearer $T_DEV"
```

反向同理。若两侧都返回 200 则说明 `SECRET_KEY` 未真正隔离，验收失败。

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 服务器磁盘 63G，构建三个镜像可能吃紧 | 构建失败 | 构建前 `docker system df` 检查；必要时 `docker builder prune` |
| 线上播种在**非空**库上执行 | 数据污染 | 线上卷全新，首次启动即空库；`DEV_SEED_DEMO_DATA=1` 的 insert-only 语义已由既有测试覆盖 |
| `172.28/172.29` 与未来其他 Compose 项目冲突 | 网段撞车 | 部署前已实测空闲；`familygraph-prod` 项目名保证网络名带前缀 |
| `cloudflared` 的 Service 直连 8100 绕过宿主 nginx | 与既有 `lyston.qzz.io` 行为不同 | 这是刻意的：既隔离又不动宿主 nginx；已由 CF 侧 Public Hostname 配置决定 |
| Provider 密钥迁移脚本含明文 | 泄漏 | 明文只在进程内存；脚本不落盘不提交；失败时回退到管理后台手工重注册 |
| 线上与开发共用 `ADMIN_INITIAL_PASSWORD` 值 | 管理员口令相同 | 用户已锁定该决策（记忆 #354），且两侧是不同的 `system_admins` 行、不同库；首登强制改密不变 |
| 误在开发 checkout 执行线上 compose | 用开发代码起线上栈 | `docker-compose.prod.yml` 放在 `deploy/production/` 且安装脚本硬编码 `--project-directory`；发布流程文档化 |
| 线上栈抢占 8100/8101 | 部署失败 | 部署前 `ss -tln` 校验；脚本 fail-fast |

## 7. 回滚形态

- **一级回滚**（保留数据）：`docker compose -p familygraph-prod stop`。
  开发环境完全不受影响，公网 `fg.lyston.qzz.io` 返回 502（与当前状态一致）。
- **二级回滚**（删栈保卷）：`down`（不删卷），线上数据保留在卷内。
- **三级回滚**（彻底移除）：`down -v` + 删目录 + 删 systemd 单元 + 删 env
  + 删 CF Public Hostname。
- 开发侧无回滚动作（本任务不修改开发任何配置与数据）。

## 8. 兼容性

- 不改任何后端/前端/agent 源码，只新增部署文件 ⇒ 无迁移、无 API 契约变化。
- 不新增 Alembic 迁移 ⇒ 无迁移序号冲突风险（多 agent 并行时的硬约束）。
- 不改宿主 nginx、不改 cloudflared 服务、不改既有 systemd 单元 ⇒
  不影响 1Panel、note.lyston.qzz.io、learngraph 等其他项目。
- 新增的 systemd 单元使用 `familygraph-prod-` 前缀，与既有 `familygraph-`
  前缀单元在名称上可区分（`systemctl --user list-units 'familygraph-*'` 会
  同时列出，运维手册中说明）。
