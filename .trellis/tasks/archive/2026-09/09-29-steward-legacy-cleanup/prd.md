# Steward 架构遗留清理：carrier 默认值、失效验证入口与陈旧描述

## 目标与用户价值

把 in-process 载体删除（S5）后残留的三类问题收敛掉，使「代码、验证入口、文档」三者与实际执行路径一致：新增插入路径不再可能静默造出无人可租的 attempt，验证脚本不再指向不存在的用例，排查时读到的注释不再描述已删除的批次设计。

本轮由用户在「空间 2 修复」之后追问「是否还有很多之前的管家架构遗留」触发。用户已同意建任务执行。

## 背景与证据边界

以下是本轮逐项核对（100 个配置项 + 生产代码 + 现行规范 + 验证脚本）确认的事实：

### 一、真实问题（会实际失效）

- **`StewardModelCall.carrier` 默认值仍是 `inproc`**：`backend/app/models/steward.py:412` 为 `default="inproc", server_default="inproc"`；迁移 `0055_steward_assist_execution_unit.py:635-637` 建列为 `NOT NULL DEFAULT 'inproc'`。当前生产唯一插入路径（`steward_assist._reserve_attempt` 的 `row_common`，`:1065`）显式写 `CARRIER_PI`，因此**尚未出错**。但 `lease_attempt` 只租 `carrier == 调用方 carrier` 的行，sidecar 只传 `pi`，所以任何忘记写 `carrier` 的新插入路径会造出**永久无人可租**的 `reserved` attempt——静默卡到租约过期后以 `unknown` 保守计费。
- **`backend/scripts/steward_e2e.py` 已无法运行**：`:206` import `StewardAssistBatch`，该模型在迁移 0055 已删除。它在 `.trellis/spec/backend/steward-action-card.md:172` 被登记为验证入口。
- **`scripts/smoke/run_controlled_acceptance.py` 的 B1/B1b 套件全部失效**：引用不存在的 `tests/test_steward_assist_deadline.py`（6 个用例）。同类失效共 10 个用例节点（另 4 个 `test_steward_assist.py::*` 用例名在 `452236b` 删载体时一并删除）。

### 二、陈旧描述（无行为影响）

生产代码 docstring 10 处仍描述已删设计（`StewardAssistBatch` / `register_batch_for_job` / `schedule_due_batch` / `launch_batch` / `recover_stuck_batches`）：

- `steward_assist.py` 模块头 `:1-32` 用四步描述「注册 → 调度/预留 → 写回 → 崩溃恢复」，其中「登记一行 `StewardAssistBatch`」「`schedule_due_batch` 选中批次」「`launch_batch` 提交线程」「`recover_stuck_batches` 恢复」**四个符号都不存在**；实际是 `plan_for_job`（同事务登记 plan + 预留全部 attempt）+ `lease_attempt`（发送门）+ 两阶段结算 + `recover_stuck_attempts`。
- `steward_assist.py:1295` 拿 `schedule_due_batch` 作对比基准（该函数已删，但对比句本身仍有信息量，需改写而非删除）。
- `maintenance.py:11`、`models/agent.py:47`、`models/agent.py:239`、`models/steward.py:289`、`agent_execution.py:302`。
- 现行规范 6 处：`steward-action-card.md:127/131/133/134/139`、`steward-candidate-evidence.md:15`。

### 三、已核对确认**不是**问题的（不要顺手改）

- **无死配置**：`config.py` 100 项里 6 项看似无消费者，实际 3 项（`ADMIN_API_HOST/PORT`、`ADMIN_JWT_SECRET_MIN_LENGTH`）由 `serve.py`/`config.py` 自身读 `os.environ`，3 项（`STEWARD_ASSIST_CANDIDATE/RANKING/EXPLANATION`）经 `platform_features.py:41` 的 `getattr(config, f"STEWARD_ASSIST_{kind.upper()}")` 间接读。
- **`_API_PATHS` 不是 in-process 残留**：`steward_assist.py:129` 保留它是为了 fence 判定 `REASON_PROVIDER_API_UNSUPPORTED`（「该 Provider 协议是否受支持」的合同，与载体无关），代码注释已说明。
- **`unknown` 状态仍有产生路径**：崩溃点③（`in_flight` + 租约过期）在 `recover_stuck_attempts` 内仍在生产生效。
- **`steward_assist._classify_transport_error` 不能删**：`record_attempt_outcome` 的 `exc` 参数在生产恒为 `None`（sidecar 上报 `error_code` 字符串而非异常对象），因此该函数在生产**不可达**、仅测试与 `steward_pi_harness` 使用；但它是 `unknown/timeout` 语义的测试锚点，删除会让该合同失去验证。
- **`settle_attempt` 无生产调用者但不是死代码**：真实路径是 `internal_agent` 的 settle hook → `record_attempt_outcome`（phase 1）→ `apply_settled_attempt`（phase 2）；`settle_attempt` 是持有事务的调用方包装，测试与未来调用方使用。

## 需求

### R1 carrier 列默认值不得再造出无人可租的行

新插入的 `steward_model_calls` 行必须落到当前唯一执行载体（`pi`）。约束要落在**数据层**（`server_default`），而不只是 ORM 默认值——否则直接 SQL 插入仍会得到 `inproc`。

- 不得收紧 `carrier IN ('inproc','pi')` 的 CHECK、不得回填历史 `inproc` 行（memory #399、0055 注释「never backfilled」）。
- 历史行的可读性不变：`inproc` 仍可读作「in-process 时代」，`lease_attempt` 的 carrier 过滤保证它们永不被租（当前 768 行 `inproc` 全部已终态，无 `reserved`/`in_flight`）。
- 若采用迁移，refusal guard 必须在任何 DDL 之前；downgrade 必须能回到 `inproc` 默认且不丢行。

### R2 验证入口必须真的能跑

被 spec 登记为验证入口的脚本不得引用不存在的模块或用例。

- `steward_e2e.py`：要么修到可运行，要么从 spec 验证入口移除并在 spec 中记录原因。**它还有第二个问题**：不启动 sidecar，因此即使修好表名，Pi 路径下的 assist 断言也无法通过（`_run_assists` 依赖 `schedule_due_attempt` + `drain_plan`，而真实执行需要 sidecar 租约）。修复必须正面处理这一点，不能只改表名让脚本"看起来能跑"。
- `run_controlled_acceptance.py`：失效的 10 个用例节点必须替换为等价的现存用例或移除该套件；不得保留指向不存在用例的条目。

### R3 陈旧描述不得再声称已删设计是现行行为

生产代码与现行规范里，凡把已删符号（`StewardAssistBatch`、`register_batch_for_job`、`schedule_due_batch`、`launch_batch`、`recover_stuck_batches`、`_post_json`、`_release_remaining_unsent`）描述为现行机制的，改为描述实际路径。保留有信息量的历史对比（如 `steward_assist.py:1295`），但必须明确标注它是历史基准。

## 验收标准

| ID | 可观察结果 | 对应需求 |
|---|---|---|
| AC-1 | 用**不带 `carrier` 的裸插入**（ORM 与直接 SQL 各一次）造行，得到的 `carrier` 为 `pi`；旧库迁移后 `server_default` 为 `pi` 且历史 `inproc` 行计数与状态完全不变 | R1 |
| AC-2 | `carrier` CHECK 仍允许 `inproc`（历史行可读），且 `lease_attempt` 对 `inproc` 行的排除行为有回归 | R1 |
| AC-3 | 迁移（若采用）在隔离 DATA_DIR 完成 upgrade → downgrade → upgrade 往返；downgrade 后默认值回到 `inproc` 且无行丢失 | R1 |
| AC-4 | `steward_e2e.py` 能真正跑完并产出证据，或已从 spec 验证入口移除且 spec 写明原因；两种情况下 spec 与实际一致 | R2 |
| AC-5 | `run_controlled_acceptance.py` 不再引用任何不存在的用例；其引用的每个节点在本地 `pytest --collect-only` 可解析 | R2 |
| AC-6 | 全仓 grep 已删符号，生产代码与现行规范中不再有把它当作现行机制的描述；剩余出现处均为历史对比（带标注）、迁移文件或归档任务 | R3 |
| AC-7 | backend 全量 pytest / ruff / mypy 通过；agent 与 frontend 未受影响（未改则说明未运行理由） | R1–R3 |

## 不在范围

- 收紧 `carrier` CHECK 或删除历史 `inproc` 行（memory #399 明确禁止）。
- 删除 `_API_PATHS`、`_classify_transport_error`、`settle_attempt`（已核实有合同或测试价值）。
- 修 `steward_e2e.py` 的业务场景覆盖（若决定保留脚本，只修到能跑；不扩展场景）。
- 任何生产环境操作；线上由用户手动发布。
- 顺手清理未在本任务核对清单内的其他遗留（超出已确认范围）。
