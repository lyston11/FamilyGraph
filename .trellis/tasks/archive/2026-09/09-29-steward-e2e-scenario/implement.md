# 实施计划：修正 steward e2e 场景的空间类型

## 当前阶段

- planning。用户已同意执行本任务。
- 根因与方案已由代码 + 现有测试 + 生产数据三方印证（见 `design.md` §1）。
- 用户审阅后启动任务。

## P0 实施前确认

- [x] 阅读 PRD/design 与 manifests。
- [x] 复核根因（**两个门**）：`share_active_household` 只匹配 `kind == "household"`（`steward.py:1533`）；且 `evaluate_recommendation` 要求双端 `identity_confirmed`（`identity_fsm.recommendation_eligible`）。用四种组合实测复现 `profile_not_confirmed` / `already_connected` 两条原因码。
- [x] 复核生产数据：`household_link` 卡片在 lineage 空间 39 张、household 空间 0 张。
- [x] 在主检出 `task.py start`，读取 task.json 的 branch/worktree_path，进入该 worktree。

## P1 打开两个门

- [x] `steward_e2e.py` 的 `register()`：注册+登录后调用 `POST /api/me/identity/confirm`，断言 200 且返回体表明确认已发生。**不得**直接改库 `profile_status`。
- [x] `steward_e2e.py`：建空间改为 `{"kind": "lineage"}`。
- [x] 检查步骤 2（成员邀请）在 lineage 空间上是否仍走同一审批链；若是，保持不动。
- [x] 检查步骤 4（`/api/connection-requests` + accept）在 lineage 空间是否仍产出 confirmed spouse SourceFact；若端点对空间类型有要求，按需调整或改用等价路径。
- [x] 检查步骤 10（撤权）与步骤 11（进程中断恢复）是否仍成立。
- [x] 逐项运行，确认脚本能推进到末尾并写出证据 JSON。

验收：AC-1。

## P2 断言恢复与变异验证

- [x] 步骤 5：把 `cards_created` 从「仅记录」恢复为真实断言（至少一张 `household_link`）。断言「存在」而非精确计数，避免脆断。
- [x] 确认步骤 7 的辅助部分**仍不断言成功**（无 sidecar），保持如实记录。
- [x] 变异验证：删掉步骤 4 的关系确认，卡片断言必须失败（证明它不是恒真）。

验收：AC-2。

## P3 文档

- [x] 脚本 docstring：说明空间类型选择的原因（`household_link` 产自家族空间，家庭空间内 R5 抑制）与覆盖范围。
- [x] `.trellis/spec/backend/steward-action-card.md` 的验证入口说明同步。

验收：AC-3。

## P4 回归

```bash
cd backend && .venv/bin/pytest -q
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd backend && .venv/bin/python scripts/steward_e2e.py    # 实跑，确认跑完并写证据
```

- [x] 断言 R5 抑制与 `recommendation_matrix` 零 diff：`git diff --stat` 不含这两个文件。
- [x] 未改 agent/frontend，说明未运行其检查的理由。

验收：AC-4。

## P5 收尾

- [x] 更新 spec（验证入口范围 + 场景空间类型）。
- [x] 记录 AC 证据与未执行的高成本检查理由。
- [x] 串行提交/集成；归档；清理 worktree 与分支。
- [x] 明确线上未操作。

## 执行结果（2026-09-29）

### 根因比设计初稿多一个门（实测修正）

设计初稿只找到「空间类型」这一个门。实施时用 `evaluate_recommendation` 对四种组合实测，
发现**首要阻塞是身份未确认**：

```
kind=household confirmed=False  eligible=False reason=profile_not_confirmed
kind=household confirmed=True   eligible=False reason=already_connected
kind=lineage   confirmed=False  eligible=False reason=profile_not_confirmed
kind=lineage   confirmed=True   eligible=True  actions=(create_household,)
```

脚本的 `register()` 只调 `/api/auth/register` + `/api/auth/login`，从未调
`/api/me/identity/confirm`；自助注册产生 `User(provisional)`，而推荐矩阵要求双端
`identity_confirmed`。生产库印证：51 个用户全部 `identity_confirmed`，`provisional` 为 0。
**两个门缺一不可**，只修任一个仍得 0 张卡。PRD/design/implement 已按实测修正。

### 顺带修掉上一任务引入的缺陷

`wait_plans` 把「还没有 plan」当成收敛：未收敛 plan 的列表在**登记前**与**收敛后**都是空，
于是它在 job 尚未跑完时就返回，后续断言读到空 plan 集合。现在要求「至少已登记一个 plan」
才算收敛。这个缺陷是上一任务引入的，本任务发现并修复。

### AC 证据

| AC | 结果 | 证据 |
|---|---|---|
| AC-1 | ✅ | 脚本跑完并写证据 JSON；`core_tick.cards_created=[1]`，经真实 tick 路径（`run_maintenance_tick` → job → 卡片）产生，非直接插库 |
| AC-2 | ✅ | 卡片断言为「存在 `household_link`」而非精确计数。变异验证 2 组：删身份确认 → 失败；空间改回 household → 失败 |
| AC-3 | ✅ | 脚本 docstring 记录两个前提与覆盖范围；`steward-recommendation-suppression.md` 补「验证脚本必须遵守」的推论 |
| AC-4 | ✅ | backend **1935 passed / 3 skipped**；ruff/mypy 通过；`steward.py` 与 `recommendation_matrix.py` **零 diff**（已用 `git diff --name-only` 断言） |

### 未执行 / 不在范围

- 未改 agent/frontend，故未运行其检查。
- 未让脚本启动 sidecar（独立工作量，已在上一任务明确排除）。
- 生产环境操作：无。线上由用户手动发布。

### 开发环境部署

`3adafd8` 已合并进 main 并在开发环境 fast-forward、重启、健康 200。**线上未操作。**

## 验收映射

| 验收 | 主要证据 |
|---|---|
| AC-1 | 脚本实跑 + 证据 JSON 中 `cards_created` 非空 |
| AC-2 | 卡片断言为真实断言 + 变异验证失败 |
| AC-3 | docstring 与 spec 一致 |
| AC-4 | backend 全量检查 + R5/矩阵零 diff |
