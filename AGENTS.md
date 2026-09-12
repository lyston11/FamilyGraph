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
