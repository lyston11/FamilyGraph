# 验收记录：线上发布脚本

状态：**全部通过**（AC4 为受控推演，原因见下）。验收时间 2026-09-20。

- 线上最终代码：`9ae41d6`（`origin/main`）
- 线上库迁移：`0053_member_approval_and_labels`（与镜像 head 一致）
- 交付物：`scripts/deploy-prod.sh` + `deploy/production/README.md` 发布章节重写

## 交付物

| 文件 | 变化 |
|---|---|
| `scripts/deploy-prod.sh` | 新增（一条命令发布 + 迁移安全闸门） |
| `deploy/production/README.md` | 发布章节重写（单条命令、安全闸门、手工等价步骤） |

实现过程中修掉 4 个自身缺陷，全部由真实执行暴露（见文末「实现期缺陷」）。

## 逐条验收证据

### AC1 单条命令完成发布 ✅

```
$ cd /home/ubuntu/fg-prod && bash scripts/deploy-prod.sh
exit=0
[deploy-prod] 4a 容器全部 healthy（4 个）
[deploy-prod] 4b 迁移版本一致：0053_member_approval_and_labels
[deploy-prod] 4c 公网 https://fg.lyston.qzz.io/api/health → 200
[deploy-prod] 4d 后台未外泄：家庭端 /admin-api/health → 404
[deploy-prod]   结果        : 成功
[deploy-prod]   发布前 sha  : 9ae41d6
[deploy-prod]   目标 sha    : 9ae41d6
[deploy-prod]   发布前备份  : /data/backups/familygraph-20260920-065659.db
```

脚本自行完成：前置校验 → 记录回滚锚 → 备份 → 快进代码 → 调安装器 →
四项校验 → 摘要。备份为真实产物（`8626176 B` 的 `.db` + `.tar.gz`，
经 `PRAGMA integrity_check`）。

### AC2 错误目录立即拒绝，无副作用 ✅

```
$ cd ~/projects/FamilyGraph && bash /tmp/deploy-prod.sh
[deploy-prod] 必须在线上工作目录 /home/ubuntu/fg-prod 内执行（当前 /home/ubuntu/projects/FamilyGraph）。
     这是防止误把开发 checkout 发布上线的硬校验；确实要换目录时用 --force-dir。
exit=1

（开发 checkout 复核：HEAD 仍为 1a1c55b，无任何改动）
```

### AC3 非法目标立即拒绝 ✅

```
# 不存在的 sha
$ bash scripts/deploy-prod.sh deadbeefcafe
[deploy-prod] 无法解析目标版本：deadbeefcafe
exit=1

# 本地存在但未推送的提交
$ <造一个未推送提交 d18869c> && bash scripts/deploy-prod.sh d18869c
[deploy-prod] 目标 d18869c 不是 origin/main 的祖先（未推送的提交不能发布上线）
exit=1

# 已推送但不在 main 上的分支提交
$ bash scripts/deploy-prod.sh 0c68710        # 当时只在 feat 分支上
[deploy-prod] 目标 0c68710 不是 origin/main 的祖先（未推送的提交不能发布上线）
exit=1
```

### AC4 迁移版本不一致必须判失败 ✅（受控推演）

**为何推演而非端到端注入**：api 容器的 CMD 是
`alembic upgrade head && exec python -m app.serve`，因此正常运行下库版本
**恒等于**镜像 head；要构造「服务 healthy 但版本不一致」在结构上不可能。
第一次尝试注入两个竞争 head，结果是 `alembic upgrade head` 自身失败、
容器起不来，走的是安装器失败路径（已记录为 AC5 的另一个已验证分支），
而不是 4b 路径。因此 4b 的真实职责是防御「升级静默未生效 / 镜像迁移陈旧」，
用隔离副本推演其判定逻辑：

```
隔离副本（从线上卷复制后改版本）: 0052_seed_lineage_membership_boundary
脚本读库命令得到 : 0052_seed_lineage_membership_boundary
脚本读 head 得到 : 0053_member_approval_and_labels
判定: 不等 → 4b 判失败 ✔
```

用的是脚本里**同一条**读库命令（`compose exec api python -c ...` 的两级兜底）
与 `alembic heads | awk '/\(head\)/'`。推演后隔离副本已删除。

### AC5 失败自动回滚，且不做 downgrade ✅

分两个分支验证，两个分支都通过：

**分支 A：迁移未前进 → 自动回滚代码**

用独立临时 origin（`/tmp/fg-gate-test`，**从未涉及真实 origin**）发布一个
`upgrade()` 必然抛错的迁移：

```
[fg-prod]  Container familygraph-prod-api-1 Error dependency api failed to start
[deploy-prod] 安装器退出码 1
[deploy-prod] 迁移未前进（仍为 0053_member_approval_and_labels），自动回滚到 9cb0fd6…
[deploy-prod] 已回滚到 9cb0fd6，服务恢复 healthy
[deploy-prod]   结果        : 失败但已自动回滚
```

**分支 C：迁移已前进 → 拒绝回滚，停下等人工决策**（最高风险形态）

构造「迁移成功推进版本 + 应用启动失败」（在 `serve.py` 的 `main()` 注入
`raise RuntimeError`）：

```
[deploy-prod] 安装器退出码 1
[deploy-prod] 线上库迁移版本已前进：0053_member_approval_and_labels → 0054_release_gate_probe_ok
[deploy-prod] 拒绝自动回滚代码：库已升级而回滚代码会造成「旧代码 + 新 schema」，
[deploy-prod] 比停在当前状态更难诊断，且可能损坏数据。人工处置选项：
[deploy-prod]   1) 修复问题并发布到更新的 commit（推荐）
[deploy-prod]   2) 用发布前快照恢复库后再回滚代码：/data/backups/familygraph-20260920-065441.db
[deploy-prod]   3) 确认真实可接受数据丢失时，自行评估并手工 downgrade（本脚本刻意不自动执行）
[deploy-prod]   结果        : 失败（迁移已前进，需人工处置）
```

关键断言：**当前 sha 未回退**（仍为 `d67f894`）、**未执行任何 downgrade**、
库版本保持已前进值。这正是闸门存在的意义。

**无 downgrade 的证据**：全脚本 `grep -c downgrade` 仅出现在注释与提示文案中，
无任何 `alembic downgrade` 调用。

### AC6 同 commit 重复执行无副作用 ✅

```
before: 51 1 1        （users / agent_providers / system_admins）
exit=0
after : 51 1 1        数据不变 OK
卷未重建 OK            （familygraph-prod_app_data CreatedAt 未变）
凭据文件未被重置 OK    （/data/bootstrap/admin-credentials mtime 未变）
```

### AC7 全流程输出零密钥泄漏 ✅

对 14 份发布日志逐一 grep 线上与开发两侧的 `SECRET_KEY` /
`AGENT_SERVICE_SECRET` / `ADMIN_JWT_SECRET` / `ADMIN_INITIAL_PASSWORD` 值：

```
所有发布日志：无密钥值泄漏 OK
```

脚本本身不读也不需要密钥值，只校验 env 文件的权限位（0600），且未启用 `set -x`。

### AC8 文档与脚本行为一致 ✅

`deploy/production/README.md` 的「发布流程」章节重写为：单条命令用法、五步流程
说明、**迁移安全闸门**专节（含三个分支处置）、手工等价步骤与「必须核对
`alembic_version`」的提醒、部署顺序硬约束。

## 实现期缺陷（全部由真实执行暴露并修复）

| # | 缺陷 | 暴露方式 | 修复 |
|---|---|---|---|
| 1 | 4a 只断言一次容器状态，而 `install-prod-automation.sh` 只等 api healthy；web/admin-web/agent 的 `start_period=10s` 使其仍在 `starting` | AC1 首次运行稳定误报失败 | 改为有界等待（`wait_all_healthy`，默认 180s），并提前发现 `exited` 就放弃 |
| 2 | 默认目标走 `git checkout <sha>` 会留 detached HEAD 且二次运行快进报 `Not possible to fast-forward` | AC6 首次运行 | 默认目标改为在 `main` 上 `merge --ff-only`；仅显式 sha 才 detached |
| 3 | `git merge --ff-only` 失败时脚本以 exit 128 静默退出，无诊断 | AC5-C 首次运行（检出自�发生分岔） | 捕获失败并打印两个 sha + 对齐命令，在动任何容器前停下 |
| 4 | api 处于 crash-loop 时 `compose exec` 失败，导致发布前备份与迁移探测在**最需要时**中止 | AC5-C 恢复阶段 | 新增 `run_in_api()`：`compose exec` 失败即退化到一次性容器挂同一数据卷；4b 的 `alembic heads` 兜底在 `/app` 下执行 |

## 测试残留与环境复原

测试用独立临时 origin（`/tmp/fg-gate-test/origin.git`）承载全部注入提交，
**真实 `origin/main` 全程未被污染**：最终 `git ls-tree origin/main` 中迁移目录
只有 `0052`/`0053`，无任何探针文件。

复原动作与核对：

```
/tmp 残留              : 0 项（临时 origin、注入脚本、隔离副本、发布日志全部删除）
fg-prod remote         : git@github.com:lyston11/FamilyGraph.git（已还原）
fg-prod sha / branch   : 9ae41d6 / main，0 项改动
探针 tag                : 0 个
探针迁移文件            : 0 个（迁移目录 57 个文件）
serve.py 注入          : 0 处
库迁移版本              : 0053_member_approval_and_labels（已从探针值精确复原）
```

最终健康（复原后）：

```
familygraph-prod-{api,agent,web,admin-web}  全部 Up (healthy)
https://fg.lyston.qzz.io/api/health          → 200
https://fg.lyston.qzz.io/admin-api/health    → 404（后台不外泄）
朱元璋 公网登录                              → 200
开发环境 api 8000                            → 200（未受影响）
开发 agent systemd                           → active
lyston.qzz.io (1Panel) / note.lyston.qzz.io  → 200 / 302（相邻站点未受影响）
线上库 users=51 providers=1 admins=1
```

测试期间在线上卷内产生的多份备份快照（共 10 份）按既有 30 天轮转策略留存，
不影响运行；如需清理可手工删除较早的 `familygraph-*.db`/`.tar.gz`。

## 未运行的高成本检查及原因

- **未跑三包完整检查套件**（`ruff`/`mypy`/`pytest`、`npm lint`/`type-check`/`test`/`build`）：
  本次只新增一个不含业务逻辑的发布脚本 + 文档，不改 backend/frontend/agent 源码；
  脚本的正确性由真实发布/回滚路径端到端验证，比静态检查更强。
- **未跑 shellcheck**：开发机与服务器均未安装（`command -v shellcheck` 不存在），
  已用 `bash -n` 做语法校验。若需要可另装后补跑。
- **未做真实 `alembic downgrade`**：这是设计上的禁止项，不是遗漏。
- **未验证 CI/CD**：线上发布刻意保持显式手工动作，不引入流水线。

## 回滚

- 脚本自身有问题：它只调用既有 `install-prod-automation.sh` 与
  `compose exec ... app.backup`，手工按 README 的「手工流程」等价执行即可；
  删除 `scripts/deploy-prod.sh` 不影响现有发布能力。
- 自动回滚误判：用 `--no-rollback` 跳过，保持现场供排查。
