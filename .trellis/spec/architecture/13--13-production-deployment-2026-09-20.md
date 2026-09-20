# 13. 线上环境部署与开发环境隔离（2026-09-20，`deploy/production/`）

### 1. Scope / Trigger

在同一台服务器上新增、修改或排查第二套（线上）FamilyGraph 栈时适用。也适用于
任何改动 `deploy/production/*`、`scripts/deploy-prod.sh`、
`scripts/install-prod-automation.sh`、`scripts/prod-backup.sh` 或线上 env 的任务。

不适用于开发环境本身的运维（那是 systemd 用户单元 + 宿主目录，见 README）。

### 2. Signatures

- 部署目录：`deploy/production/`，由 `docker-compose.prod.yml`（叠加配置）、
  `familygraph-prod.env.example`（模板）、`README.md`（运维手册）组成。
- 安装入口：`scripts/install-prod-automation.sh`（幂等，负责构建与启动）。
- 发布入口：`scripts/deploy-prod.sh`（在安装器前后加校验、备份与回滚）。
- 备份入口：`scripts/prod-backup.sh`（由 `familygraph-prod-backup.timer` 调用）。
- 线上 env：`~/.config/familygraph/familygraph-prod.env`（0600；与开发的
  `familygraph.env` 并列，互不覆盖）。
- compose 项目名固定 `familygraph-prod`；卷 `familygraph-prod_app_data`；
  网络 `familygraph-prod_{frontend,backend,admin}`；
  容器 `familygraph-prod-{api,web,admin-web,agent}`。
- 宿主端口：家庭 web `127.0.0.1:8100`、管理 web `127.0.0.1:8101`。
  线上 api（8000/8001/8002）与 agent 不发布任何宿主端口。

### 3. Contracts

**隔离不变量（六维，任一被打破即为回归）**

1. 代码：独立 clone（如 `/home/ubuntu/fg-prod`），不共享开发 checkout；
   不启用 `familygraph-code-sync.timer`（发布是显式动作）。
2. 数据：线上库在 named volume 内；开发库在宿主目录。
3. 密钥：`SECRET_KEY` / `AGENT_SERVICE_SECRET` / `ADMIN_JWT_SECRET` 必须与
   开发值不同。这决定 JWT 互不承认，也决定 Provider 密钥密文必须重加密。
4. 端口：线上只占 8100/8101（回环），开发保持 8000/8001/8002/18080。
5. 进程：线上是容器，开发是 systemd 用户单元；停任一侧不影响另一侧。
6. 镜像：标签带 `-prod` 后缀，避免与基文件默认标签混用。

**回源路径**

Cloudflare 隧道（token 模式）的 Public Hostname `fg.<zone>` 的 Service 直连
`http://127.0.0.1:8100`，**不经宿主 nginx**。因此新增线上入口不得改动
`/etc/nginx/sites-enabled/`，也不得影响同一隧道的其他 hostname（如 1Panel、
note）。TLS 由 Cloudflare 边缘终止，回源为明文。

**管理后台**

- 不得为管理后台创建任何 Public Hostname、DNS 记录或隧道路由。
- 唯一入口是宿主回环 `127.0.0.1:8101` + SSH 隧道。
- 线上 `ADMIN_API_HOST` 绑定 admin 容器网络静态 IP（172.29.0.10），
  `INTERNAL_AGENT_API_HOST` 绑定 backend 静态 IP（172.28.0.10）；两者由
  基文件固定，不得改为通配地址（`serve.py` 会 fail-closed 拒启）。
- 家庭端（8100）对 `/admin-api/*` 必须返回普通 404。

**端口反转的写法**

叠加文件必须用 `ports: !override` 而不是追加列表，否则会与基文件继承的端口
合并（8080/8081 被一并发布）。清空用 `ports: !override []`。
已验证 Compose v5.1.4 支持该标签。

**环境变量透传**

基文件 `docker-compose.yml` 中各服务的 `environment` 是**显式白名单**，
未列出的键在 `.env` 里写了也不会进入容器。新增任何后端 config 键并要求可从
部署环境调节时，必须同时在基文件 `environment` 中透传（给与
`backend/app/config.py` 相同的默认值）。

**运维约束**

- 备份必须经 `python -m app.backup`（SQLite online backup API + integrity_check），
  禁止运行期 `cp` WAL 主库。
- 线上发布顺序：含新增事件类型时后端先于 agent sidecar；回退顺序相反。
- 安装脚本必须对以下情况 fail-closed：密钥与开发环境相同、env 权限非 0600、
  8100/8101 被**非本 compose 项目**的进程或容器占用。
- systemd **user manager** 可能不含 `docker` 组（manager 启动早于用户加组）。
  涉及 docker 的用户单元必须能自行处置该情形（现为 `sg docker` 单次重执行）。

**发布入口：`scripts/deploy-prod.sh`**

线上发布用一条命令（`bash scripts/deploy-prod.sh`），它在既有
`install-prod-automation.sh` 前后加四件事：位置/目标校验、发布前备份、
四项发布后校验、失败回滚。两条合同不得放松：

1. **迁移安全闸门**：发布失败时先判断库迁移是否已前进。未前进 → 自动回滚代码；
   **已前进 → 拒绝自动回滚**并停下要求人工决策（旧代码配新 schema 比
   「新版本起不来」更难诊断且可能损坏数据）。读不到库版本时保守地不回滚。
2. **从不自动执行 `alembic downgrade`**。

其他约束：

- 目标必须是 `origin/main` 的祖先（禁止发布未推送提交）；默认目标在 `main`
  上快进，避免线上检出留下无基线的 detached HEAD。
- 只允许在线上工作目录（默认 `/home/ubuntu/fg-prod`）执行。
- 四个容器的健康检查必须在**有界等待**后再断言：安装器只等 api healthy，
  其余服务的 `start_period=10s`/`interval=30s` 会让立即断言稳定误报。
- api 处于 crash-loop 时 `compose exec` 会失败，而此时正是最需要读库/备份的时刻；
  涉及 api 的操作必须有一次性容器挂同一数据卷的兜底。

### 4. Required validation

```bash
# 配置解析（务必在服务器上跑：需要 docker 与 compose plugin）
docker compose -p familygraph-prod --env-file ~/.config/familygraph/familygraph-prod.env \
  -f docker-compose.yml -f deploy/production/docker-compose.prod.yml config
# 断言：api 无 ports；web 127.0.0.1:8100；admin-web 127.0.0.1:8101；
#       卷名为 familygraph-prod_app_data；backend 网络 internal:true

# 隔离不变量
diff <(grep ^SECRET_KEY= ~/.config/familygraph/familygraph.env) \
     <(grep ^SECRET_KEY= ~/.config/familygraph/familygraph-prod.env)   # 必须不同
curl -s -o /dev/null -w '%{http_code}\n' https://fg.<zone>/admin-api/health  # 期望 404
docker port familygraph-prod-api-1 familygraph-prod-agent-1                  # 期望空

# 幂等
bash scripts/install-prod-automation.sh   # 重复执行必须成功且不改变数据

# 发布（含安全闸门；重复执行必须成功且无副作用）
cd /home/ubuntu/fg-prod && bash scripts/deploy-prod.sh
```

完整逐条验收证据见
`.trellis/tasks/archive/2026-09/09-20-production-deployment-isolated/verification.md`
与 `.trellis/tasks/archive/2026-09/09-20-prod-release-script/verification.md`。
