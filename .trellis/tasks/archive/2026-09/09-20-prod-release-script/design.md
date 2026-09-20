# 技术设计：线上发布脚本

## 1. 边界

**做什么**：新增 `scripts/deploy-prod.sh`，在服务器 `/home/ubuntu/fg-prod` 内执行，
把「备份 → 拉取 → 重建 → 迁移核对 → 健康校验 → 失败回滚」串成一条命令。

**不做什么**：不新增服务、不改 compose、不改后端源码、不引入 CI。
脚本只编排既有原语：

| 既有原语 | 用途 |
|---|---|
| `scripts/install-prod-automation.sh` | 前置校验 + `compose build` + `up -d` + 等 healthy |
| `compose exec api python -m app.backup` | 发布前一致性快照 |
| `git` | 决定发布哪个 commit |
| `docker compose ps` / `curl` | 健康与隔离校验 |

关键设计约束：**不复制 `install-prod-automation.sh` 的逻辑**。它是发布动作的
唯一实现，本脚本只在其前后加「锚点、备份、校验、回滚」。

## 2. 流程

```
start
 │
 ├─ 0 位置与前置校验（fail-fast）
 │    · 必须 cd 在 fg-prod（用 docker-compose.prod.yml 存在 + git remote 匹配判定）
 │    · env 文件存在且 0600
 │    · docker 可用
 │    · 目标 sha 必须是 origin/main 的祖先（禁止发布未推送改动）
 │    · 记录回滚锚：PREV_SHA + PREV_ALEMBIC
 │
 ├─ 1 发布前备份
 │    · compose exec api python -m app.backup   （失败即中止）
 │    · 记录快照文件名
 │
 ├─ 2 切换代码
 │    · git fetch origin main
 │    · git checkout <target>（或 merge --ff-only origin/main）
 │
 ├─ 3 重建并启动
 │    · bash scripts/install-prod-automation.sh
 │       ↳ 内部含构建、up -d、等 api healthy、备份定时器对齐
 │
 ├─ 4 发布后校验（任一失败 → 进 5）
 │    · 4a 四个容器全部 healthy
 │    · 4b 线上库 alembic_version == 镜像内 head
 │    · 4c https://fg.lyston.qzz.io/api/health == 200
 │    · 4d https://fg.lyston.qzz.io/admin-api/health == 404（后台不外泄）
 │
 ├─ 5 失败处理
 │    · 若库迁移版本已前进 → 停止，报告，要求人工决策（不 downgrade）
 │    · 否则 → checkout PREV_SHA → 重跑安装器 → 复验 → 报告已回滚
 │
 └─ 6 结构化摘要（sha 前/后、alembic 前/后、备份文件、各校验结果）
```

## 3. 「迁移是否前进」的判定

这是自动回滚的**安全闸门**，必须准确：

- 发布后校验读两次 `alembic_version`：
  - 发布前（写进 `PREV_ALEMBIC`）
  - 发布后（尝试读；容器 crash-loop 时可能读不到 → 视为「未知」）
- 判定：
  - `POST == PREV` → 迁移未前进，**允许自动回滚代码**。
  - `POST != PREV` → 迁移已前进。**禁止自动回滚**（回滚代码但库已升级会造成
    代码/库不一致，比停在当前状态更危险）。脚本停下并报告人工处置选项。
  - 读不到 POST（容器起不来）→ 再尝试用宿主上的只读副本读卷内库文件；
    仍读不到则保守地**不自动回滚**并报告。

读卷内库文件的方式：`docker run --rm -v familygraph-prod_app_data:/data alpine`
不行（镜像里无 sqlite3）。改用：
`docker compose exec -T api python -c "..."` 复用镜像内的 python 与 sqlite3 模块——
容器起不来时不可用。此时退回：
- 用一个一次性 python 容器挂同一卷（镜像 `familygraph-prod-api:0.1.0` 已有 python）：
  `docker run --rm -v familygraph-prod_app_data:/data familygraph-prod-api:0.1.0 \
     python -c "import sqlite3; print(sqlite3.connect('/data/db/app.db').execute('select version_num from alembic_version').fetchone())"`

这条路径不依赖 api 容器 running，是关键兜底。

## 4. `install-prod-automation.sh` 的复用与冲突处理

该脚本已经在做：

- 前置校验（env 权限、密钥与开发不同、端口占用）
- `compose build` + `up -d` + 等 healthy（失败时打印日志并 `fail`）
- 备份定时器对齐与自检输出

**问题**：它在 api 不 healthy 时自己就 `fail`（非零退出）。本脚本需要区分
「安装器失败」与「安装器成功但后置校验失败」，因此：

- 把安装器当黑盒：捕获退出码。
- 非零退出 → 直接进「失败处理」，但仍要先判定迁移是否前进（这是安全闸门，
  必须独立于安装器成功与否）。
- 零退出但 4b/4c/4d 失败 → 同样进失败处理。

## 5. 幂等与可重入

- 目标 sha == 当前 sha 时：仍执行（安装器本身幂等），但摘要中明确标注
  `no-op release`，且**不跳过备份**（备份无害且提供锚点）。
- 中断重跑：脚本无自有状态文件；所有状态从 git HEAD、容器状态、库版本实时读取，
  因此重跑等价于一次新发布，天然可重入。
- 不写任何锁文件（避免残留导致后续发布被永久阻塞）。

## 6. 密钥纪律

- 脚本只读 env 文件的**权限与存在性**，不读取也不需要密钥值。
- 不打印 env 内容、不 `set -x`（会泄漏）。
- 唯一可能接触到密钥的地方是调用 `install-prod-automation.sh`（它内部会
  对比 dev/prod 密钥是否相同，但只输出键名，不输出值）。

## 7. 参数

```
scripts/deploy-prod.sh [<sha|ref>] [--no-rollback] [--skip-backup]
```

- 无参数：发布 `origin/main` 最新。
- `<sha|ref>`：发布指定目标（回滚旧版本或灰度时用）。
- `--no-rollback`：校验失败时不自动回滚，只报告（逃生阀）。
- `--skip-backup`：跳过发布前备份（默认禁止；仅在明确知道无需备份时用，
  且脚本会打印警告）。

帮助文本中说明「为什么默认要备份、为什么默认要回滚」。

## 8. 风险

| 风险 | 对策 |
|---|---|
| 误在开发 checkout 执行 | 步骤 0 硬校验：必须是 git 仓库且存在 `deploy/production/docker-compose.prod.yml`，且当前分支/HEAD 可控；不在 `/home/ubuntu/fg-prod` 时要求显式 `--force-dir` 并打印警告 |
| 自动回滚掩盖真实问题 | 回滚后打印完整失败证据；迁移前进时**拒绝回滚** |
| 备份占用磁盘 | 复用线上既有 30 天轮转（`prod-backup.sh` 已在做），本脚本不额外定义保留策略 |
| 校验 4d 误判 | 家庭端 `/admin-api/health` 期望 404；若返回 200 说明路由外泄，属真实回归，失败正确 |
| 安装器输出过长吞掉摘要 | 安装器输出重定向到临时日志，摘要里给出日志路径 |

## 9. 兼容性

- 不改任何既有文件的行为；纯新增脚本 + README 文档更新。
- 不新增迁移 → 无迁移序号冲突。
- 与 `install-prod-automation.sh` 的关系是「调用方」，不改变其契约；
  若安装器后续改签名，只需调整一处调用。
