# FamilyGraph Agent Guide

## 当前工作流

本项目使用 Trellis 管理任务、规范和会话上下文。开始工作前阅读：

- `.trellis/workflow.md`：任务分流、阶段和完成条件
- `.trellis/spec/guides/index.md`：跨模块通用约定
- 与改动包对应的 `.trellis/spec/<package>/index.md`
- 当前任务目录中的 `prd.md`、`design.md`、`implement.md`（存在时）

Codex 可直接使用 `.agents/skills/` 中的 `trellis-start`、`trellis-before-dev`、`trellis-check` 等技能；可用的 Trellis 命令优先于手工改写任务状态。没有明确任务时，小范围改动可直接完成；跨模块、需求不清或需要持续跟踪的工作先创建 Trellis task。

任务生命周期使用 `.trellis/scripts/task.py`：

```bash
python3 ./.trellis/scripts/task.py create "标题" --description "一句话说明" --slug <slug>
python3 ./.trellis/scripts/task.py start <task-dir>
python3 ./.trellis/scripts/task.py current --source
python3 ./.trellis/scripts/task.py validate <task-dir>
python3 ./.trellis/scripts/task.py finish
python3 ./.trellis/scripts/task.py archive <task-dir>
```

任务状态、PRD、设计、实现记录和核验记录写入 `.trellis/tasks/`；规范更新写入 `.trellis/spec/`。`.agent-notes/` 仅保留历史记录，不是当前流程入口。

## 并行任务与 Git 隔离（多 agent 硬性规则）

多个 agent 共用一个工作区曾导致 `reset` 抹掉 main 上已提交的改动。并行工作必须遵守以下规则：

- 一个任务一个分支 + 一个 linked worktree。`task.py start` 的 `after_start` 钩子会自动创建 `feat/<任务目录名>` 分支、在仓库旁 `../fg-<任务目录名>` 建 worktree，并把两者写入 task.json 的 `branch` / `worktree_path`。钩子成功时无输出，以 task.json 为准；写代码前必须先 `cd` 进该 worktree，不得在主检出里改业务代码。
- agent 在自己的分支上只允许 commit；禁止 `reset --hard`、`rebase`、`push -f`、切换到非本任务的分支。需要 main 新代码时 `git fetch` 后把 `origin/main` merge 进自己分支。
- 任何 agent 不得改写 main 或他人分支的历史；历史整理只由人在专门时机做。
- `task.py` 生命周期命令（current / finish / archive 等）在启动任务的主检出里执行；代码改动和提交都在任务 worktree 里。任务确实不需要隔离时（如纯文档小改），完成后用 `git worktree remove ../fg-<任务目录名>` 清理。
- 只有文件/模块不相交的任务才并行；共享同一批文件、migration 序号、SQLite 库或端口的任务一律串行。
- 小步提交并尽早 push（或本地 `git branch backup/<任务>` 钉住）；远端是抗本机历史重写的唯一兜底。
- 发现提交被抹掉时：先停掉所有并行 agent，从 `git reflog` 定位丢失提交，用 `git branch rescue/<名> <sha>` 钉住，核对后 cherry-pick 或取回文件。
- 分支合并回 main 由人（或单一串行集成通道）执行，集成时检查 migration 序号冲突。
- 任务收尾必含清理，不得把残留 worktree 留给用户：分支合并进 main、`task.py archive` 完成后，执行集成的 agent 必须立即 `git worktree remove ../fg-<任务目录名>` 并 `git branch -d feat/<任务目录名>`，并在交付说明中报告清理结果。删除前置条件：分支已合并进 main、worktree 无未提交代码改动（`.venv`、`node_modules` 等未跟踪构建产物可忽略）；条件不满足时保留现场并向用户说明原因，禁止用 `--force` / `git branch -D` 掩盖未合并或未提交的工作。

任务分支与合并流程：

```bash
# 开发：在任务 worktree 里小步 commit、尽早 push
# 集成：主检出切回 main，一次只合一个分支
git checkout main && git merge feat/<任务目录名>
# 检查 migration 序号冲突，跑受影响范围的最小充分检查，然后 push
python3 ./.trellis/scripts/task.py archive <任务目录名>   # archive commit 落在 main
# 收尾必做：删除 worktree 和分支，不要留给用户
git worktree remove ../fg-<任务目录名> && git branch -d feat/<任务目录名>
```

- 集成点唯一且串行：main 只在主检出的 merge 中前进；`archive` 在主检出执行，其校验只看元数据（`branch` ≠ `base_branch`、分支已记录），分支删除后归档仅告警不阻塞。
- 并行中的其他任务分支用 `git fetch` 后 `git merge origin/main` 跟进，不 rebase。

## 代码范围

- `backend/`：FastAPI、SQLAlchemy、Alembic、pytest、ruff、mypy
- `frontend/`：Vue 3、Vite、TypeScript、Pinia、Vitest
- `system-admin-frontend/`：独立管理员前端，仅访问 `/admin-api`
- `shared/`：跨端共享类型或协议
- `scripts/`：开发、smoke 和维护脚本

改动前确认实际归属模块和现有复用点；不要为了绕过边界在另一层复制逻辑。涉及 API、认证、权限、迁移、跨 listener 或跨前后端契约时，沿代码、迁移和测试一起检查。

## 开发与验证

完整开发环境按场景只启动一种：

```bash
./scripts/dev-up-remote.sh   # 远程后端/数据库，本地前端和 SSH 隧道
./scripts/dev-up.sh          # 全本地 listener、数据库和前端
```

脚本结束前必须检查健康端点；日志在 `.dev-logs/`。按改动范围选择最小充分检查：

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
cd frontend && npm run lint && npm run type-check && npm test && npm run build
cd system-admin-frontend && npm run lint && npm run type-check && npm test && npm run build
./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json
```

只运行相关检查时，在交付说明中写明未运行的高成本检查及原因。smoke 退出码 `2` 表示环境阻塞，不算通过。

## 数据与安全约束

- 密钥只通过环境变量提供，不写入代码、日志、任务文件或提交。
- SQLite 使用 WAL；服务运行期间禁止直接复制主库，备份使用 `python -m app.backup` 或对应 Compose 命令。
- 附件下载必须经过后端授权端点；管理员 API 与家庭 API 保持 listener 和 JWT 域隔离。
- 迁移先在隔离数据库执行 `alembic upgrade head`，再运行受影响测试。
- 不修改用户已有的工作区、任务和未提交业务改动；发现冲突先缩小范围并保留证据。

### Spec / Research 上下文治理

- `.trellis/spec/**/index.md` 才是 Spec 索引入口；`.trellis/workspace/index.md` 与 `.trellis/workspace/<用户名>/index.md` 是会话索引，不是 Spec 索引。
- Spec 叶文件只定义一个可独立适用的合同；`index.md` 只做范围、适用性、叶链接和验证入口的路由，不复制正文。
- 复杂 Research 使用 `research/index.md` → `<topic>-summary.md` → `research/evidence/*` 分层；默认上下文只注入 summary，证据按需读取。
- `implement.jsonl` / `check.jsonl` 只允许引用精确 Spec 叶文件和 Research summary，不得引用 `AGENTS.md`、任何索引、源代码或 `research/evidence/*`。
- 历史任务在治理 adoption marker 前保持冻结，不为满足新规则迁移。

## Trellis 受管区

<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->
