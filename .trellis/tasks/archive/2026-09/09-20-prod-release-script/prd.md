# 线上发布脚本：把 fg-prod 发布固化成单条命令

## Goal

把线上栈（`/home/ubuntu/fg-prod`）的发布从「一串手工步骤」收敛为**一条幂等命令**，
并让关键风险（漏迁移、拿错机器、回滚无锚点）由脚本而非人看住。

## 背景（实测现状）

线上于 2026-09-20 上线（`https://fg.lyston.qzz.io`，compose 项目 `familygraph-prod`）。
当前发布流程是手工的：

```bash
cd /home/ubuntu/fg-prod
git fetch origin main
git checkout <sha>
bash scripts/install-prod-automation.sh
```

已暴露/已知的坑：

1. **磁盘 pull 到 ≠ 服务加载**。`install-prod-automation.sh` 的 `compose up -d`
   会按镜像内容变化重建，但若 `git checkout` 只改了 Dockerfile 之外的东西
   （例如只在 `.env` 改开关），用户容易以为已生效而实际没重建。
2. **迁移是容器 CMD 里自动跑的**（`alembic upgrade head && exec python -m app.serve`），
   一旦迁移失败，容器会 crash-loop，而脚本只在 `up -d` 之后等 healthy，
   失败时留下的是**已停的旧容器 + 起不来的新容器**，无自动回退。
3. **无回滚锚点**：发布前不记录「上一个可用 sha」，出问题时只能翻 git log。
4. **`/home/ubuntu/fg-prod` 不是任务 worktree**，手工 `git checkout <sha>` 会进入
   detached HEAD，下次 `git fetch` 后没有清晰基线。
5. 历史教训（记忆已知）：远端曾出现「git 已到含新迁移的提交、但生产库
   `alembic_version` 还停在旧版本」，导致接口 500。发布脚本必须在发布后
   **显式核对库的迁移版本**，而不是只看容器 healthy。

## Requirements

### R1 单条命令

- 提供一个脚本，一条命令完成一次线上发布；除「选哪个 commit」外无必需参数。
- 默认目标 commit = `origin/main` 的最新提交；允许显式指定 sha 用于回滚或灰度。

### R2 发布前的前置校验（fail-fast，不留下半成品）

- 校验执行位置是线上工作目录（`/home/ubuntu/fg-prod`），不是开发 checkout；
  若在错误目录执行必须立即拒绝，不得静默发布错误代码。
- 校验线上 env 文件存在且权限 0600。
- 校验目标 commit 在 `origin/main` 上可达（禁止发布未推送的本地改动）。
- 记录**回滚锚**：当前运行的 commit + 当前线上库的 `alembic_version`。

### R3 发布前备份

- 发布前必须自动产出一份线上库的一致性快照（走 `app.backup`，在线 backup API）。
- 备份失败即中止发布（不做无备份的发布）。

### R4 发布动作可审计

- 输出并记录：发布前 sha、发布后 sha、库迁移版本（前/后）、各容器健康状态。
- 不打印任何密钥值。

### R5 发布后校验

- 容器全部 healthy。
- 线上库 `alembic_version` == 代码中 `head`（与镜像内迁移文件一致），不一致即失败。
- `https://fg.lyston.qzz.io/api/health` 返回 200。
- 家庭端对 `/admin-api/*` 仍返回普通 404（不回归后台隔离）。

### R6 失败可回滚

- 上述任一校验失败时，脚本必须**自动回到回滚锚**（旧 commit 重新构建启动），
  并明确报告「已回滚到 <sha>，库迁移版本为 <v>」。
- 若迁移已前进（库已升级），脚本不得尝试 `alembic downgrade`，
  必须停下来如实报告并要求人工决策（数据破坏风险优先）。

### R7 幂等与可重入

- 对同一 commit 重复执行是安全的：不重复发布、不破坏数据、不重置凭据。
- 中断后重跑能从当前状态继续，不留下半成品容器。

## Acceptance Criteria

- AC1：在 `/home/ubuntu/fg-prod` 用一条命令发布 `origin/main`，脚本自行完成
  备份 → 拉取 → 重建 → 迁移核对 → 健康校验，并打印结构化摘要。
- AC2：在错误目录（如开发 checkout）执行时立即拒绝，不产生任何改动。
- AC3：指定一个非法/未推送的 sha 时立即拒绝并说明原因。
- AC4：发布后线上库 `alembic_version` 与镜像内迁移 `head` 一致；不一致时脚本
  判定失败（可用人为构造的假迁移文件验证，验证后清理）。
- AC5：故意让新版本起不来（例如注入一个必然失败的迁移）时，脚本自动回滚到
  发布前 commit，容器恢复 healthy，公网 health 恢复 200，且**不做任何 downgrade**。
- AC6：同一 commit 连续执行两次：第二次成功且无副作用（不重复备份以外的写入、
  库行数与迁移版本不变、管理员凭据文件不被重置）。
- AC7：脚本在失败路径下仍不打印任何密钥值。
- AC8：脚本行为与 `deploy/production/README.md` 的发布章节一致（文档同步更新）。

## Out of Scope

- 自动 CI/CD、GitHub Actions、定时自动发布（线上发布**刻意**保持显式动作）。
- 蓝绿/金丝雀/多实例并行发布。
- 把开发环境（systemd 裸机）纳入同一脚本。
- 自动 `alembic downgrade`（明确禁止）。
- 改动线上功能开关取值。

## 回滚

- 脚本自身出问题：它只调用既有 `install-prod-automation.sh` 与
  `compose exec ... app.backup`，手工按 README 发布章节等价执行即可；
  删除本脚本不影响现有发布能力。
- 脚本引入的自动回滚若误判：保留 `--no-rollback` 逃生开关（记录在帮助里）。
