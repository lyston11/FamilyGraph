# 13. 线上环境部署与开发环境隔离（2026-09-20，`deploy/production/`）

### 1. Scope / Trigger

在同一台服务器上新增、修改或排查第二套（线上）FamilyGraph 栈时适用。也适用于
任何改动 `deploy/production/*`、`scripts/install-prod-automation.sh`、
`scripts/prod-backup.sh` 或线上 env 的任务。

不适用于开发环境本身的运维（那是 systemd 用户单元 + 宿主目录，见 README）。

### 2. Signatures

- 部署目录：`deploy/production/`，由 `docker-compose.prod.yml`（叠加配置）、
  `familygraph-prod.env.example`（模板）、`README.md`（运维手册）组成。
- 安装入口：`scripts/install-prod-automation.sh`（幂等）。
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
```

完整逐条验收证据见
`.trellis/tasks/archive/2026-09/09-20-production-deployment-isolated/verification.md`。
