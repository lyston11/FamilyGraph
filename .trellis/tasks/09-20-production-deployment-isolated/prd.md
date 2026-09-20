# 线上环境部署：fg.lyston.qzz.io 与开发环境完全隔离

## Goal

在 lyston 服务器上部署第二套 FamilyGraph 线上栈，经 Cloudflare 隧道以
`https://fg.lyston.qzz.io` 对公网提供家庭端服务；系统管理员后台不暴露公网、
仅经 SSH 隧道可达；线上与现有开发环境在**数据、密钥、端口、代码、进程、卷**
六个维度完全隔离；保留朱元璋等 mock 演示账号可直接登录使用。

## 背景（实测现状）

- 家庭端当前是「本地 Mac 前端 + 服务器后端」：服务器 systemd 用户单元
  `familygraph-api.service`（8000/8001/8002）+ `familygraph-agent.service`（18080），
  代码在 `/home/ubuntu/projects/FamilyGraph`，`PUBLIC_API_HOST=127.0.0.1`，
  只经 launchd SSH 隧道访问。
- 开发库 `/home/ubuntu/projects/FamilyGraph/backend/data/db/app.db`，
  `alembic_version=0051`，`DEV_SEED_DEMO_DATA=1`（51 个演示成员，PIN 123456）。
- `lyston.qzz.io` 的权威 NS 是 `tate/kami.ns.cloudflare.com`，即该域本身是
  Cloudflare 上独立托管的 zone；`cloudflared`（token 模式，tunnel id
  `69504be9-a5e1-4f84-8374-638888e8496e`）已在本机运行，入口统一走
  `127.0.0.1:80`（nginx 按 Host 分发）。
- 用户已手工在 Cloudflare 添加 Public Hostname：`fg.lyston.qzz.io →
  http://127.0.0.1:8100`。实测 `dig fg.lyston.qzz.io` 返回 CF 边缘 IP，
  `curl https://fg.lyston.qzz.io/` 返回 **502**（隧道路由已通、8100 无服务）。
- 宿主端口占用：8000/8001/8002（开发 api）、18080（开发 agent）、8080（1Panel）、
  4224/5432/6379/6333/6334/9000/9001/18000/18001/20241 已被其他项目占用。
  `8100`、`8101` 空闲；docker 网段 `172.28.0.0/24`、`172.29.0.0/24` 空闲。
- 服务器：Ubuntu 24.04 / 4C / 23G / 96G（余 63G），Docker 29 + Compose v5.1.4
  （支持 `!override` 端口标签），`sudo` 免密，`docker.service` 已 enable。

## Requirements

### R1 数据隔离（硬要求）

- 线上必须使用**独立的 SQLite 库文件**，与开发库无任何共享路径。
- 线上必须使用**独立的 Docker named volume**（含 db / uploads / backups /
  bootstrap 凭据），与开发环境互不可见。
- 线上数据库的初始内容由线上自己的 `DEV_SEED_DEMO_DATA=1` 播种产生，
  不从开发库拷贝用户数据。

### R2 密钥与认证域隔离（硬要求）

- 线上使用**全新生成的** `SECRET_KEY` / `AGENT_SERVICE_SECRET` /
  `ADMIN_JWT_SECRET`，与开发环境**值不相同**。
- 因 `SECRET_KEY` 不同，线上与开发的 JWT 互不承认：开发签发的 access/refresh
  token 在线上必须被拒，反之亦然。
- 密钥只存于服务器 `~/.config/familygraph/familygraph-prod.env`（0600），
  不进仓库、不进日志、不写入任务文件。

### R3 端口与进程隔离

- 线上栈不得占用开发环境任何端口（8000/8001/8002/18080）。
- 线上容器只发布两个宿主**回环**端口：家庭 web `127.0.0.1:8100`、
  管理 web `127.0.0.1:8101`；线上 api 与 agent **不发布任何宿主端口**。
- 线上进程与开发进程互不影响：重启/停止线上栈不得导致开发栈不可用。
- 线上栈的 Docker 网络与卷命名必须带独立项目前缀，避免与开发或服务器上
  其他 Compose 项目（dbx / learngraph / fast-note-sync-service 等）冲突。

### R4 代码与发布隔离

- 线上代码来自**独立 clone**（与开发 checkout 不同目录），不共享工作区。
- 线上**不启用**服务器上已有的 30 分钟自动 commit/pull/push 定时器；
  线上发布是显式动作（拉取指定 commit → 重建 → 上线）。
- 线上使用的镜像标签与开发镜像标签不同名，避免误用。

### R5 公网入口

- `https://fg.lyston.qzz.io` 必须返回家庭端 SPA，`/api/*` 正确反代到线上后端。
- TLS 由 Cloudflare 边缘终止；回源为明文 HTTP 到宿主回环端口。
- 线上后端仍保持 `PUBLIC_API_HOST=127.0.0.1`（不直接暴露宿主公网端口）。

### R6 系统管理员后台仅内网

- 管理后台**不得**出现在任何公网 hostname、DNS 记录或隧道路由中。
- 管理后台仅经宿主回环 `127.0.0.1:8101` + SSH 隧道访问。
- 管理 API（8002）在线上容器内只绑定 admin 内部网络接口，宿主与公网均不可直达。

### R7 mock 演示账号继续可用

- 线上启用 `DEV_SEED_DEMO_DATA=1`，首启自动播种「明皇室 + 九户外戚世家」
  演示数据（51 个成员）。
- 朱元璋等演示成员必须能在 `https://fg.lyston.qzz.io` 用演示 PIN 直接登录
  （`pin_must_change=False`），无需改密。
- 播种为 insert-only 收敛，重复启动不得重复建号、不得改动既有行。

### R8 Assistant 与管家链路可用

- 线上必须注册与开发同规格的 Provider（`liu-dada` / `gpt-5.6-sol` /
  `openai-responses` / `https://api.liu-dada.com/v1`），使 assistant 与
  steward 的模型调用可用。
- Provider 密钥的**明文不得出现在命令行、日志、任务文件或仓库中**；
  跨环境迁移必须密文到密文（开发 `SECRET_KEY` 解密 → 线上 `SECRET_KEY` 加密）。
- 线上 `familygraph-prod-agent` 容器必须在运行，否则 assistant run 永远
  `queued`。

### R9 功能档位与开发一致

- 线上功能开关取值与开发环境一致（实测值）：
  `AGENT_RUNTIME_ENABLED=1`、`PERSONAL_FAMILY_VIEW_ENABLED=1`、
  `STEWARD_ENABLED=1`、`STEWARD_WORKER_ENABLED=1`、
  `STEWARD_ASSIST_CANDIDATE/RANKING/EXPLANATION/TERMINOLOGY=1`、
  `MEMORY_ENABLED=1`、`RAG_ENABLED=1`、`DEV_SEED_DEMO_DATA=1`。
- `ADMIN_INITIAL_PASSWORD` 使用部署配置的既有口令（用户已锁定该决策），
  首登强制改密规则不变。

### R10 可重复安装与数据保全

- 线上栈的安装必须是幂等脚本，重装服务器后一条命令恢复全部自动化
  （与 `scripts/install-server-automation.sh` 的既有哲学一致）。
- 线上必须有不依赖人工的定期 SQLite 一致性备份（online backup API，
  禁止运行期 `cp` 主库），带保留窗口。

## Acceptance Criteria

- AC1：`curl -f https://fg.lyston.qzz.io/api/health` 返回 `{"status":"ok"}`；
  浏览器打开 `https://fg.lyston.qzz.io` 得到家庭端登录页。
- AC2：线上库与开发库是**不同文件**（容器内 `/data/db/app.db` 属于
  `familygraph-prod_app_data` 卷；开发库仍是
  `/home/ubuntu/projects/FamilyGraph/backend/data/db/app.db`），且开发库的
  行数/修改时间在本任务前后**未变化**。
- AC3：线上库 `alembic_version` = `head`；演示成员数 = 51（朱元璋等）。
- AC4：用演示 PIN 在 `https://fg.lyston.qzz.io` 登录朱元璋成功，且不要求改密。
- AC5：开发环境签发的 token 在线上 API 被拒（401），线上签发的 token 在
  开发 API 被拒（401）；两侧 `SECRET_KEY` 与 `ADMIN_JWT_SECRET` 值不同。
- AC6：宿主 `curl http://127.0.0.1:8002/admin-api/health`（开发）与
  线上 admin listener 均不可直达；线上 `8101` 只在回环监听；
  `https://fg.lyston.qzz.io/admin-api/health` 返回普通 404；
  `dig` 中不存在任何指向线上管理后台的公网 hostname。
- AC7：停止线上栈（`docker compose down`）后，开发环境
  `http://127.0.0.1:8000/api/health` 仍返回 200；反之开发栈重启不影响线上。
- AC8：线上 agent 容器运行中，且 `/internal/agent/*` 未暴露在宿主端口
  （宿主 `curl 127.0.0.1:8001` 命中的是开发实例，线上容器无宿主端口映射）。
- AC9：线上 assistant 能真实完成一次模型调用（run 从 queued → succeeded），
  Provider 密钥跨环境迁移后仍可解密使用。
- AC10：线上定期备份定时器已启用，且能产出一份通过 `PRAGMA integrity_check`
  的快照文件。
- AC11：重跑安装脚本是幂等的：不重复建卷、不重复建管理员、不重置既有数据、
  不改变已完成的上线状态。

## Out of Scope

- 把开发库的**用户业务数据**迁移到线上（线上只保留演示种子数据）。
- 为线上管理后台配置 Cloudflare Access 或 WireGuard/Tailscale（本期按 SSH
  隧道访问；若用户后续要求「免隧道多设备访问」再单独立项）。
- 给 `lyston.qzz.io`（1Panel 面板）或 `note.lyston.qzz.io` 做任何改动。
- 修改线上/开发的功能开关语义或后端业务逻辑。
- 高可用、多实例、横向扩容、SQLite → Postgres 迁移。

## 回滚

- 停线上栈：`docker compose -p familygraph-prod down`（不影响开发）。
- 彻底移除：`down -v` 删除线上卷，`rm -rf /home/ubuntu/fg-prod`，
  删除 systemd 用户单元与 `~/.config/familygraph/familygraph-prod.env`，
  在 Cloudflare 删除 `fg` 的 Public Hostname。
- 开发环境无需回滚（本任务不修改开发侧任何配置或数据）。
