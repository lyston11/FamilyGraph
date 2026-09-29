# 实施计划：Steward 架构遗留清理

## 当前阶段

- planning。用户已同意执行本任务。
- 设计阶段已用隔离实验否决迁移路线（见 `design.md` §2），据此调整了 R1 的实现方式。
- 用户审阅本计划后启动任务。

## P0 实施前确认

- [x] 阅读 PRD/design 与 manifests。
- [x] 复核设计 §2 的实验结论：在隔离 DATA_DIR 重跑一次 `batch_alter_table(recreate="always")` + `alembic downgrade 0043`，确认失败可复现。若结论不成立（例如改用 `recreate="auto"` 后链路通过），回到设计重新评估迁移路线，不要沿用本计划的结论。
- [x] 确认生产 `steward_model_calls` 中 `carrier <> 'pi'` 且 `status IN ('reserved','in_flight')` 的行数仍为 0（决定历史行是否需要处置）。
- [x] 在主检出 `task.py start`，读取 task.json 的 branch/worktree_path，进入该 worktree。

## P1 R1：carrier 默认值（零 schema 变更）

- [x] `models/steward.py`：ORM `default="inproc"` → `"pi"`；`server_default` 保持 `"inproc"`，并在注释写明「改它需要重建该表，而重建会把列内 FK 规范化为表级并丢掉 `ON DELETE SET NULL`，破坏 0044 的 downgrade；因此用结构性测试守住，不用迁移」。
- [x] 新增结构性测试（放 `test_steward_child_run_acceptance.py` 或 `test_steward_execution_unit_migration.py`，按现有同类断言的归属选择）：
  - 解析生产代码 AST，断言每一处 `StewardModelCall(...)` 调用都显式传 `carrier`；
  - 断言生产代码不存在对 `steward_model_calls` 的裸 SQL 插入。
- [x] 变异验证：删掉 `_reserve_attempt` 里的 `"carrier": CARRIER_PI`，测试必须失败。
- [x] 行为回归：造一个**不带 carrier** 的行，确认 ORM 默认给 `pi`，且 `lease_attempt(carrier="pi")` 能租到它。
- [x] 保留回归：`lease_attempt` 不租 `inproc` 行（既有覆盖，确认仍通过）。

验收：AC-1、AC-2。

## P2 R2：验证入口

### steward_e2e.py

- [x] 删除对 `StewardAssistBatch` 的 import 与用法（改为 `StewardAssistPlan`/`StewardModelCall`，或删除已失效的 assist 断言段）。
- [x] 在脚本 docstring 顶部明确写：**本脚本不再驱动模型辅助**（Pi 路径需要 sidecar，本脚本不启动），assist 的端到端验证入口改为 pytest 套件（列出具体文件）。
- [x] 确认脚本能通过 import/语法检查：`.venv/bin/python -c "import ast;ast.parse(open('scripts/steward_e2e.py').read())"`，且 `python scripts/steward_e2e.py` 不再因 import 失败而崩（若仍因缺 sidecar 无法完成，docstring 必须说明）。
- [x] `spec/backend/steward-action-card.md:172` 更新验证入口：移除 `steward_e2e.py` 或标注其范围，补上真实替代入口。

### run_controlled_acceptance.py

- [x] 对 10 个失效节点逐条处理：能等价替换的换成现存用例；不能的删除该条目并保持套件意图。
- [x] 保留一条自检：脚本引用的每个 `tests/*.py::test_*` 节点都必须能被 `pytest --collect-only` 解析（可在本地用脚本验证，不必写进代码）。

验收：AC-4、AC-5。

## P3 R3：陈旧描述

- [x] 按 `design.md` §4 的表逐处改写；每处只改注释/docstring，不动行为代码。
- [x] `steward_assist.py:1295` 保留历史对比但标注「已删除的旧调度」。
- [x] 改写后重跑全仓 grep（已删符号），确认剩余出现处仅为：迁移文件、归档任务、明确标注的历史对比。

验收：AC-6。

## P4 最小充分回归

```bash
cd backend && .venv/bin/pytest -q
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
# 结构性测试与新增断言的变异验证
cd backend && .venv/bin/pytest tests/test_steward_child_run_acceptance.py tests/test_steward_execution_unit_migration.py -q
```

- [x] 无 schema 变更，因此**不需要**迁移往返；若最终仍改了迁移，则必须补隔离 DATA_DIR 的 upgrade→downgrade→upgrade。
- [x] 未改 agent/frontend，说明未运行其检查的理由。
- [x] ruff 的既有 `test_invitation_reachability.py` 错误不在范围内（若仍在，如实报告为既有）。

验收：AC-7。

## P5 收尾

- [x] 更新 spec：验证入口变更 + carrier 默认值决策。
- [x] 逐项记录 AC 证据与未执行的高成本检查理由。
- [x] 串行提交/集成；归档；清理 worktree 与分支。
- [x] 明确线上未操作。

## 执行结果（2026-09-29）

### AC 证据

| AC | 结果 | 证据 |
|---|---|---|
| AC-1 | ✅ | ORM `default` 已是 `pi`；结构性测试断言 app/ 每处 `StewardModelCall(...)` 都经关键字或 `**row_common` 命名 carrier；行为用例断言裸插入得 `pi`。变异验证 3 组：删 `row_common["carrier"]`、把 ORM 默认改回 `inproc`、插入一条裸 SQL —— 各自让对应用例失败 |
| AC-2 | ✅ | schema 未改动，`ck_smc_carrier` 仍允许 `inproc`；`lease_attempt` 的 carrier 过滤回归通过；生产 768 行 `inproc` 全部已终态、`reserved` 非 `pi` 为 0 |
| AC-3 | N/A | 未采用迁移。否决实验已在设计 §2 记录，并在 P0 复核：基线 `downgrade 0043` 通过；`recreate="always"` 与 `"auto"` 均失败于 `unknown column "run_id"`；`"always"` 还静默丢掉 carrier CHECK |
| AC-4 | ✅ | `steward_e2e.py` 现在能跑完并写出证据 JSON；辅助步骤如实报 `assist_reservation_observed: false`；spec 已写明其范围（确定性内核）与真实替代入口（4 个 pytest 套件）。**注**：脚本仍不覆盖 assist，这是刻意如实降级而非修好 |
| AC-5 | ✅ | 10 个失效节点全部替换为现存等价用例；23 个引用节点 `--collect-only` 全部解析（1938 tests collected） |
| AC-6 | ✅ | 全仓 grep 已删符号：生产 2 处、现行规范 4 处，逐条核实**全部带历史标注**（`原 StewardAssistBatch` / `已删除的旧调度` / `随…一起删除` / `旧名`） |
| AC-7 | ✅ | backend **1935 passed / 3 skipped**；ruff 我的文件全过；mypy 通过。未改 agent/frontend，故未运行其检查 |

### 过程中发现的额外漂移（均已修）

`steward_e2e.py` 除了已知的 `StewardAssistBatch` import，还撞上 4 个独立漂移，说明它已长期未被运行：

1. 邀请端点现在要求必填 `relation_label`；
2. 09-20 起的审批链要求 owner 先 `approve` 再受邀人 `accept`；
3. 场景把三人放进同一 household，`household_link` 按 R5 被正确抑制 → 该场景不再产生辅助工作；
4. `launch_due` 在受限线程内异步执行，一次 tick 不足以让 job 收敛，需要继续 tick。

第 3 点证实了设计判断：该脚本即使修好 import 也无法验证 Pi 辅助。

### 未执行 / 不在范围

- 迁移往返（无 schema 变更）。
- agent / frontend 检查（未改这些包）。
- `test_invitation_reachability.py` 的既有 E501/F841（不在范围，未修）。
- 生产环境操作（线上由用户手动发布）。

### 开发环境部署

`6b2c580` 已合并进 main 并在开发环境（`/home/ubuntu/projects/FamilyGraph`，systemd 用户单元）fast-forward、重启、健康 200。运行进程确认 `ORM default=pi` / `server_default=inproc`；无残留 `in_flight` 或 `running`。**线上未操作。**

## 验收映射

| 验收 | 主要证据 |
|---|---|
| AC-1 | 结构性测试 + 裸插入行为 + 变异验证 |
| AC-2 | `lease_attempt` 的 carrier 过滤回归 + CHECK 仍在（schema 未动） |
| AC-3 | 不适用（未采用迁移）；设计 §2 记录否决实验 |
| AC-4 | steward_e2e 可导入/可解析 + spec 入口更新 |
| AC-5 | 10 个失效节点的替换/删除 + 全节点 collect 校验 |
| AC-6 | grep 结果 + 逐处改写 diff |
| AC-7 | backend 全量 pytest/ruff/mypy |
