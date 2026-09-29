# 技术设计：Steward 架构遗留清理

## 1. 设计目标与约束

落实 PRD R1–R3。核心约束：**不为一个默认值承担 schema 重建风险**。

## 2. R1 carrier 默认值：为什么不用迁移（已实测否决）

### 实测证据

在隔离 DATA_DIR 上对 `steward_model_calls` 执行 `batch_alter_table(recreate="always")` 改 `server_default`：

```
重建后列定义（被规范化）：
  run_id INTEGER,
  CONSTRAINT ck_smc_carrier CHECK (carrier IN ('inproc','pi')),
  FOREIGN KEY(run_id) REFERENCES agent_runs (id)      ← 表级，且 ON DELETE SET NULL 丢失
原始定义：
  run_id INTEGER REFERENCES agent_runs (id) ON DELETE SET NULL   ← 列内
```

随后 `alembic downgrade 0043` 失败：

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) error in table
steward_model_calls after drop column: unknown column "run_id" in foreign key definition
[SQL: ALTER TABLE steward_model_calls DROP COLUMN run_id]
```

同一 downgrade 在**未重建**的基线上通过。即：重建把列内 FK 规范化为表级 FK，0044 的 downgrade 无法再 `DROP COLUMN run_id`。

附加证据：`batch_alter_table` 重建时若不同时重新声明 `create_check_constraint`，CHECK 会**静默消失**（实测重建后 `carrier IN ('inproc','pi')` 不再存在）。

### 决策

**不迁移。** 改为：

1. ORM `default="inproc"` → `"pi"`（对齐当前唯一载体）。
2. 保留 `server_default="inproc"` 不变，并在模型处注明原因（改它需要重建，而重建破坏 0044 downgrade 链）。
3. 用**结构性测试**守住真正的失败模式：断言生产代码中每一处 `StewardModelCall(...)` 构造都显式传 `carrier`；并断言全仓不存在生产用裸 SQL 插入。

理由：结构性测试比 DB 默认值**更强**——默认值只在忘记传参时静默兜底，而测试会在新增路径漏写时**立刻失败**。PRD R1 的意图是「不得再造出无人可租的行」，不是「必须有某个 server_default 字面量」。

### 历史行不变

`inproc` 保留在 CHECK 内、历史 768 行不回填（memory #399、0055「never backfilled」）。生产数据实测：`inproc` 行全部已终态（succeeded 647 / unknown 85 / failed 20 / skipped 9 / degraded 7），**无 `reserved`/`in_flight`**，因此不存在被卡住的历史行。`lease_attempt` 的 `carrier == 调用方 carrier` 过滤继续保证它们永不被租。

## 3. R2 验证入口

### `steward_e2e.py`

已确认两点，必须正面处理而非只改表名：

- `:206` import `StewardAssistBatch`（0055 已删）；
- **不启动 sidecar**（无 `Popen`、无 `18080`、无 `FG_AGENT_ROLE`），而 Pi 路径下 assist 必须由 sidecar 经 `/internal/agent/steward/attempts/lease` 租取。所以即使修好表名，`_run_assists` 驱动的 assist 断言也无法产生真实 attempt。

决策：**从 spec 验证入口移除，并在 spec 记录原因与替代入口**。理由：把它修到「真能跑」等于重建一个隔离端到端 harness（起 sidecar、装依赖、控端口），那是独立任务的工作量；而它的核心场景（业务流 → job/PFV/卡片 → 建议审阅 → 撤权 → 恢复）已由 pytest 覆盖：

- `test_steward_child_run_acceptance.py`（受控 E2E + 行为等价 + fence 调用点）
- `test_steward_pi_carrier_terminology.py`（网关可达 + egress 审计 + 崩溃/孤立租约收敛）
- `test_steward_pi_carrier_remaining_kinds.py`（四 kind 围栏）
- `steward_pi_harness.py`（驱动一次 Pi attempt：lease → context → settle）

同时删除脚本内对已删符号的引用，使其至少在语法/导入层不再声称可用；不保留一个「看起来是入口但跑不了」的文件。

### `run_controlled_acceptance.py`

10 个失效用例节点（1 个文件不存在 ×6、4 个用例名已删）。逐条替换为等价的现存用例；无法等价替换的条目删除，并保持该套件的检查意图（F-R2 总截止语义）由现存用例覆盖。不得保留指向不存在节点的条目。

## 4. R3 陈旧描述

改写而非删除。保留信息量的历史对比必须显式标注为历史基准。范围：

| 文件 | 处 | 处理 |
|---|---|---|
| `steward_assist.py:1-32` | 模块头四步描述 | 改写为实际路径：`plan_for_job`（同事务登记 plan + 预留全部 attempt）→ `lease_attempt`（发送门）→ 两阶段结算 → `recover_stuck_attempts` |
| `steward_assist.py:1295` | 与 `schedule_due_batch` 对比 | 保留对比，标注「已删除的旧调度」 |
| `steward_assist.py:94/111/443/1242/1616/1701` | 提到 in-process 载体 | 改为「已删除的 in-process 载体」或改述为接收端职责 |
| `maintenance.py:11` | 「恢复并调度模型辅助批次（StewardAssistBatch）」 | 改述为恢复 attempt 中间态 + sidecar 租取 |
| `models/agent.py:47,239` | 注释引用 `StewardAssistBatch` | 改为 `StewardModelCall` |
| `models/steward.py:289` | 「原 StewardAssistBatch」 | 保留（明确是历史沿革） |
| `agent_execution.py:302` | 注释引用 `register_batch_for_job` | 改为 `plan_for_job` |
| `steward-action-card.md:127/131/133/134/139` | `StewardAssistBatch`/`schedule_due_batch`/`_post_json`/`_release_remaining_unsent` | 改写为现行合同 |
| `steward-candidate-evidence.md:15` | 签名含 `batch: StewardAssistBatch` | 改为 `plan: StewardAssistPlan` |

## 5. 兼容与回退

- 无 schema 变更、无迁移、无 wire 变更。
- 回退 = revert 本任务提交。ORM 默认值回退不影响既有行（新行由显式传参决定）。
- 不改 `_API_PATHS`、`_classify_transport_error`、`settle_attempt`（已核实有合同/测试价值）。

## 6. 风险

- 结构性测试若写得太宽（例如只 grep `carrier` 出现次数）会变成恒真断言；必须解析 AST 并定位**每个构造调用的实参**，且用变异验证（删掉某处 `carrier=` 必须失败）。
- 文档改写不得顺手改动行为代码；每一处只改注释/文档字符串。
- 验证入口移除后必须确保 spec 仍指向可用的替代入口，否则等于降低可验证性。
