# E1 执行单元重构 — 实施记录

> 技术权威：父任务 `09-25-steward-pi-child-run-design/design.md`（重写版）。
> 本文件是**实施清单与结果**，勾选项表示已在 `feat/09-25-steward-execution-unit` 上完成并验证。

## 1. 迁移 `0055_steward_assist_execution_unit`（重写 S1 的 0055）

- [x] refusal guards（6 条）先于任何 DDL（memory #398）。
- [x] `steward_assist_batches` → `steward_assist_plans`（`RENAME TO` + 收窄为纯快照）。
- [x] `steward_model_calls`：加 `lease_owner`/`lease_until`/`next_attempt_at`/`carrier`；
      `batch_id` → `plan_id`（改名保留行）；加 `ix_smc_due (status, next_attempt_at)`。
- [x] `agent_runs`：kind 扩展 + `session_id` 可空 + `ck_agent_runs_scope_binding`。
- [x] `context_builds.account_id` 可空 + `ck_context_builds_account_binding`。
- [x] downgrade：守卫先行；逆序恢复；`in_flight` 行状态归一化，否则恢复后的批次状态机无法表达。
- [x] 迁移测试：空库、合法存量、逐条 guard、`upgrade → downgrade → upgrade` 往返（隔离 `DATA_DIR`）。

**实测的 6 条 SQLite 行为**（不是假设，每条都塑造了迁移写法）：trigger body 引用列时拒绝 DROP COLUMN；
表级 FK 子句引用列时也拒绝（故 `source_batch_id` 改名而非删除）；表级 CHECK 引用列时拒绝（故重建而非
就地收窄）；删父表在 FK 开启时会级联删光子行（会抹掉整个 attempt 账本，故重建时精确关闭外键）；
`RENAME TABLE` 会改写引用它的 FK 子句；`RENAME COLUMN` 保留行。

## 2. 模型层

- [x] `StewardAssistPlan`（原 Batch 收窄）、`StewardModelCall`（执行单元）、`StewardRun` 删除
      （`run_id` UNIQUE 反查即绑定，无需第二张表同步）。

## 3. 调度层（单一链路）

- [x] `plan_for_job`：同事务登记 plan + 预留全部 attempt（`reserved`, `next_attempt_at=now`）。
- [x] `lease_attempt`：按 space 计数在途 attempt → 选到期候选 → **sweep 栅栏** → 置 `in_flight`。
- [x] `settle_attempt`：两段事务（先持久化结果，再应用产物）——合并会让写回失败连结果一起回滚，
      丢掉已付费的模型答案（崩溃点④）。
- [x] `recover_stuck_attempts`：④ 补应用 → ③ 过期租约 → `unknown`（保守计费、不重发）。
- [x] `_apply_product`：单 attempt 应用（从 `_apply_batch` 的 for 循环抽出）。
- [x] 删除 `execute_batch` / `schedule_due_batch` / `launch_batch` / `_execute_in_own_session` /
      `_reserve_due_attempts` / `_apply_batch` / `_finish_batch_after_attempts` /
      `_release_remaining_unsent`（随批次收尾一起消失）。
- [x] `_budget_state` 仍按 job 累计（查询走 plan→job）。

## 4. 载体抽象

- [x] `services/steward_carrier.py`：`AssistCarrier` Protocol、`InprocCarrier`、`carrier_for()`。
- [x] `PiCarrier` 骨架：`execute` 抛 `NotImplementedError`（E3 实现）。
- [x] `carrier` 是 attempt 的字段，调用方按 carrier 分派，调度层无 `if kind`。

## 5. 配置

- [x] `STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`（默认 2，上界 8）。
- [x] `STEWARD_ASSIST_<KIND>_CARRIER` ×4（`inproc|pi`，默认 `inproc`，未知值 fail-closed）。
- [x] 移除 `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`；**同名变量在 backend / sidecar / compose 三处
      一并改名**（否则 sidecar 仍在读一个后端已不读的变量，「同名同值」合同静默失效）。

## 6. 内部协议

- [x] `POST /internal/agent/steward/attempts/lease`（取代 `/steward/jobs/lease`）。
- [x] `_settle_steward_run` 经 `on_settled` 同事务调 `settle_attempt`（避免双终态）。
- [x] `heartbeat_child_run` 同一立即事务续 run 与 attempt lease。
- [x] `_authorize_steward_run` / context 门禁 / 事件门禁绑 `model_call_id`。

## 7. 验收结果

| 门 | 断言 | 结果 |
|---|---|---|
| G1-a | 行为等价：4 种 kind 全走 inproc，`run_id` 全 NULL | 通过 |
| G1-b | 迁移往返（隔离 `DATA_DIR`） | 通过 |
| G1-c | per-space 并发：两空间同时可租；同空间第二个被**预算**挡下 | 通过（变异测试确认） |
| G1-d | fence 调用点集合 = {lease_attempt, settle_attempt, recover_stuck_attempts} | 通过（AST 断言） |
| G1-e | 发送门 sweep：被栅栏拦下的 attempt 不阻塞兄弟 | 通过（变异测试确认） |

全量检查：

```bash
cd backend && .venv/bin/python -m pytest -q   # 1919 passed, 3 skipped
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd agent && npm run type-check && npm run lint && npm test && npm run build   # 181 passed
docker compose config --quiet
```

## 8. 本轮修掉的真实缺陷

1. **发送门未闭合**（首提交遗留）：栅栏从 `schedule_due_attempt` 移出后没有在 `lease_attempt` 补上
   sweep，导致被栅栏拦下的 attempt 永不退休——空间永远停在 `reserved`，且 `plan_error_code` 永远为空。
   三个降级用例（provider 不可用 / 云同意撤销 / 要求本地）因此失败。修法是补 sweep，不是放宽断言。
2. **并发开关只改了一半**：后端改名后 sidecar 与 compose 仍在读旧名，实际效果是 sidecar 预算可被
   调大而服务端不会多放行任何工作。
3. **`schedule_due_attempt` 与 `lease_attempt` 双发送门**：同时存在时两侧可能对「哪个 attempt 被退休」
   判断不一致，故发送门只保留 `lease_attempt` 一处。

## 9. 未做（明确不在 E1 范围）

- Pi 载体执行（E3）、sidecar `KindAdapter` 拆分（E2）、candidate/ranking/explanation 迁移（E4）、
  删除 inproc 与开关（E5）。
- `tests/test_invitation_reachability.py` 在 main 上既有 2 条 ruff 违规（E501 / F841），与 E1 无关，未触碰。

## 10. 回滚点

E1 是架构性改动，无运行时开关可回退：需 revert 提交。迁移在有 `kind='steward'` 行时按设计中止 downgrade。
