# 实施计划：线上环境部署（fg.lyston.qzz.io）

## 前置确认（已实测，勿重复）

- CF Public Hostname `fg.lyston.qzz.io → http://127.0.0.1:8100` 已由用户配置完成；
  `dig` 返回 CF 边缘 IP，`curl` 返回 502（隧道通、无服务）。
- 宿主 `8100` / `8101` 空闲；docker 网段 `172.28.0.0/24`、`172.29.0.0/24` 空闲。
- Docker Compose v5.1.4 支持 `!override`，`ports: !override []` 可清空继承端口。
- 开发环境：systemd user 单元 + `/home/ubuntu/projects/FamilyGraph`，
  `alembic_version=0051`，51 个演示成员，`SECRET_KEY` 等存于
  `~/.config/familygraph/familygraph.env`。
- 服务器 `sudo` 免密；`docker.service` 已 enable。

## 检查清单

### 阶段 A：仓库侧（本地开发机）

- [ ] A1 建任务分支/worktree（`task.py start` 的 `after_start` 钩子自动创建）
- [ ] A2 新增 `deploy/production/docker-compose.prod.yml`
- [ ] A3 新增 `deploy/production/familygraph-prod.env.example`
- [ ] A4 新增 `scripts/install-prod-automation.sh`
- [ ] A5 新增 `deploy/production/README.md`（运维手册）
- [ ] A6 本地校验：`docker compose -f docker-compose.yml -f deploy/production/docker-compose.prod.yml config`
      （在服务器上跑，需要 docker；本地无 docker 则跳过并在记录中说明）
- [ ] A7 提交并 push（线上要从 GitHub 拉代码）

### 阶段 B：服务器侧部署

- [ ] B1 记录部署前基线：开发库 `app.db` 的 mtime/size、`alembic_version`、
      `users` 行数；开发环境 `curl 127.0.0.1:8000/api/health`
- [ ] B2 `git clone` 到 `/home/ubuntu/fg-prod`（独立 clone，不用现有 checkout）
- [ ] B3 生成 `~/.config/familygraph/familygraph-prod.env`：
      全新 `SECRET_KEY` / `AGENT_SERVICE_SECRET` / `ADMIN_JWT_SECRET`
      （`openssl rand -hex 32`），其余键按 PRD R9 取值，`chmod 600`
- [ ] B4 跑 `bash scripts/install-prod-automation.sh`（构建 + 启动 + 装备份定时器）
- [ ] B5 校验容器状态：4 个容器 Up，`api` healthy
- [ ] B6 校验迁移与种子：线上库 `alembic_version=head`，演示成员 51 个
- [ ] B7 Provider 密钥跨环境迁移（密文→密文，明文不落盘）→ 校验可解密
- [ ] B8 写入 `agent_space_provider_settings`（assistant + steward，与开发同规格）
- [ ] B9 公网验收：`https://fg.lyston.qzz.io/api/health` 200、SPA 可达
- [ ] B10 演示登录验收：朱元璋 + PIN 123456 经公网登录成功、不要求改密

### 阶段 C：隔离验收（本任务的核心验收）

- [ ] C1 数据隔离：线上库与开发库是不同文件；开发库 mtime/size/行数未变
- [ ] C2 密钥隔离：两侧 `SECRET_KEY`/`ADMIN_JWT_SECRET` 值不同；
      开发 token 打线上 401，线上 token 打开发 401
- [ ] C3 端口隔离：线上仅 8100/8101 在回环；开发 8000/8001/8002/18080 不变
- [ ] C4 进程隔离：`docker compose -p familygraph-prod stop` 后开发 API 仍 200
- [ ] C5 管理后台隔离：`https://fg.lyston.qzz.io/admin-api/health` 返回普通 404；
      `8101` 仅回环；无任何公网 hostname 指向管理后台
- [ ] C6 备份验收：定时器已启用，产出的快照通过 `PRAGMA integrity_check`
- [ ] C7 幂等验收：重跑安装脚本，卷/管理员/数据不变，上线状态保持

### 阶段 D：收尾

- [ ] D1 更新 `.trellis/HANDOFF.md` 或对应 spec（若产生可复用约定）
- [ ] D2 `task.py archive`，合并分支进 main，清理 worktree
- [ ] D3 报告清理结果与未运行的高成本检查

## 验证命令

```bash
# ---- 阶段 B 基线（部署前，务必先记录）----
ssh lyston 'stat -c "%s %y" ~/projects/FamilyGraph/backend/data/db/app.db; \
  sqlite3 ~/projects/FamilyGraph/backend/data/db/app.db \
  "select version_num from alembic_version; select count(*) from users;"'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/health

# ---- 阶段 B 部署 ----
ssh lyston 'cd ~/fg-prod && bash scripts/install-prod-automation.sh'
ssh lyston 'docker compose -p familygraph-prod ps'
ssh lyston 'docker compose -p familygraph-prod exec -T api \
  sqlite3 /data/db/app.db "select version_num from alembic_version; select count(*) from users;"'

# ---- 阶段 B 公网 ----
ssh lyston 'curl -s -f https://fg.lyston.qzz.io/api/health'
ssh lyston 'curl -s -o /dev/null -w "%{http_code}\n" https://fg.lyston.qzz.io/'

# ---- 阶段 C 隔离 ----
# C1 数据
ssh lyston 'stat -c "%s %y" ~/projects/FamilyGraph/backend/data/db/app.db'  # 与基线一致
ssh lyston 'docker volume ls | grep familygraph-prod'
# C2 密钥
ssh lyston 'grep -c "SECRET_KEY" ~/.config/familygraph/familygraph-prod.env'
ssh lyston 'diff <(grep ^SECRET_KEY= ~/.config/familygraph/familygraph.env) \
                <(grep ^SECRET_KEY= ~/.config/familygraph/familygraph-prod.env) \
           && echo "SAME (FAIL)" || echo "DIFFERENT (OK)"'
# C3 端口
ssh lyston 'ss -tlnp | grep -E ":8100|:8101"'
# C4 进程
ssh lyston 'docker compose -p familygraph-prod stop && \
            curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/health && \
            docker compose -p familygraph-prod start'
# C5 管理后台
ssh lyston 'curl -s -o /dev/null -w "%{http_code}\n" https://fg.lyston.qzz.io/admin-api/health'  # 期望 404
ssh lyston 'ss -tlnp | grep ":8101" | grep -v "127.0.0.1:8101" && echo "LEAK (FAIL)" || echo "loopback only (OK)"'
# C6 备份
ssh lyston 'systemctl --user list-timers "familygraph-prod-*" --no-pager'
ssh lyston 'docker compose -p familygraph-prod exec -T api \
  python -c "import sqlite3,glob; f=sorted(glob.glob(\"/data/backups/*.db\"))[-1]; \
  print(f, sqlite3.connect(f).execute(\"PRAGMA integrity_check\").fetchone()[0])"'
```

## 风险点与回滚锚

| 节点 | 风险 | 回滚动作 |
|---|---|---|
| B3 生成 env | 误写开发 env | 只写 `familygraph-prod.env`，绝不 touch `familygraph.env`；写完 `diff` 确认开发文件未变 |
| B4 构建 | 磁盘不足（63G 余量） | `docker system df` 预检；失败则 `docker builder prune` 后重试 |
| B7 密钥迁移 | 明文泄漏 / 解密失败 | 脚本不落盘不打印；失败回退到管理后台手工重注册 Provider |
| B9 公网 | 502 持续 | 检查 8100 是否监听、容器是否 healthy；不改 CF 配置（那是用户侧） |
| C4 进程隔离测试 | 误停开发栈 | 只对 `-p familygraph-prod` 操作；停前确认 project 名 |

## 未运行检查的说明口径

- 本任务不改 backend/frontend/agent 源码 ⇒ 不跑三包完整检查套件；
  仅跑受影响范围（compose config 校验 + 部署后真实健康/隔离验证）。
- 若本地无 docker，`docker compose config` 在服务器上执行并在记录中注明。
