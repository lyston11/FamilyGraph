# 实施计划：线上环境部署（fg.lyston.qzz.io）

## 前置确认（已实测，勿重复）

- CF Public Hostname `fg.lyston.qzz.io → http://127.0.0.1:8100` 已由用户配置完成；
  `dig` 返回 CF 边缘 IP，`curl` 返回 502（隧道通、无服务）。
- 宿主 `8100` / `8101` 空闲；docker 网段 `172.28.0.0/24`、`172.29.0.0/24` 空闲。
- Docker Compose v5.1.4 支持 `!override`，`ports: !override []` 可清空继承端口。
- 开发环境：systemd user 单元 + `/home/ubuntu/projects/FamilyGraph`，
  `alembic_version=0053`，51 个演示成员，`SECRET_KEY` 等存于
  `~/.config/familygraph/familygraph.env`。
- 服务器 `sudo` 免密；`docker.service` 已 enable。
- systemd **user manager** 环境不含 `docker` 组（manager 启动早于加组）——
  涉及 docker 的 user unit 必须处理这一点。

## 检查清单

### 阶段 A：仓库侧（本地开发机）

- [x] A1 建任务分支/worktree（`task.py start` 的 `after_start` 钩子自动创建）
- [x] A2 新增 `deploy/production/docker-compose.prod.yml`
- [x] A3 新增 `deploy/production/familygraph-prod.env.example`
- [x] A4 新增 `scripts/install-prod-automation.sh` + `scripts/prod-backup.sh`
- [x] A5 新增 `deploy/production/README.md`（运维手册）
- [x] A6 服务器上校验 `docker compose config` 解析（项目名/卷名/端口反转全部正确）
- [x] A7 提交并 push（线上要从 GitHub 拉代码）

### 阶段 B：服务器侧部署

- [x] B1 记录部署前基线：开发库 size/mtime/`alembic_version`/行数；dev api 200
- [x] B2 `git clone` 到 `/home/ubuntu/fg-prod`（独立 clone，非现有 checkout）
- [x] B3 生成 `~/.config/familygraph/familygraph-prod.env`（3 个全新密钥，0600）
- [x] B4 跑 `install-prod-automation.sh`（构建 + 启动 + 备份定时器）
- [x] B5 4 个容器 Up/healthy
- [x] B6 线上库 `alembic=head`、演示成员 51、朱元璋 `pin_must_change=0`
- [x] B7 Provider 密钥跨环境迁移（密文→密文，明文不落盘）→ 解密验证通过
- [x] B8 `agent_platform_defaults` + `agent_space_provider_settings` 与开发同构
- [x] B9 公网 `https://fg.lyston.qzz.io/api/health` 200、SPA 200
- [x] B10 朱元璋经公网登录成功、不要求改密

### 阶段 C：隔离验收

- [x] C1 数据隔离（inode 不同、开发库行数不变）
- [x] C2 密钥隔离 + 跨域 token 双向 401
- [x] C3 端口隔离（线上仅 8100/8101 且绑回环；api/agent 无宿主端口）
- [x] C4 进程隔离（停线上不影响开发；重启恢复）
- [x] C5 管理后台隔离（公网 404、仅回环、无公网 DNS、容器内绑内网 IP）
- [x] C6 备份（unit 成功、快照 `integrity_check=ok`）
- [x] C7 幂等（重跑计数不变、卷未重建、密钥仍可解密）

### 阶段 D：收尾

- [x] D1 写 `verification.md`（逐条 AC 的可复现证据）
- [ ] D2 `task.py archive`，合并分支进 main，push，清理 worktree
- [ ] D3 报告清理结果与未运行的高成本检查

## 验证命令

见 `verification.md` 的「逐条验收证据」——每条 AC 都附实测命令与输出。
部署与验收的实际执行记录在该文件，本文件只保留计划与勾选状态。

## 实际执行中修正的三个问题

| 问题 | 性质 | 修正 |
|---|---|---|
| admin 镜像 `vite build` ENOENT（`shared/brand-tokens.css` 在上下文外） | 仓库既有缺陷（09-11 引入，裸机开发未暴露） | 上下文改仓库根 + 拷 token 到对应深度 + 新增根 `.dockerignore` |
| compose 静默丢弃 5 个 env 键 | 仓库既有缺陷 | 按后端默认值补齐透传 |
| 备份 unit `docker: permission denied` | 环境约束（user manager 无 docker 组） | `prod-backup.sh` 内 `sg docker` 单次重执行 |
| 重跑安装器被自己占用的端口挡住 | 安装器缺陷 | 端口预检区分自身容器与外部占用 |

## 未运行检查的说明口径

- 本任务不改 backend/frontend/agent 源码 ⇒ 不跑三包完整检查套件；
  受影响面由「compose config 解析 + 镜像真实构建 + 部署后真实健康/隔离/
  模型调用验收」覆盖，详见 `verification.md` 末节。
