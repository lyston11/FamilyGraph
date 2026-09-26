# Steward 模型辅助执行单元重构 — 实施计划

> 前置：`design.md`（重写版）是唯一技术权威。
> 本文件是**实施任务清单**。用户裁定「全部重写重构」，故 S1 的 `0055_steward_child_run` 与
> `steward_assist.py` 的调度层**直接重写**，不做收敛迁移、不留共存分支。

---

## 0. 总览：为什么分四步而不是五步

第一轮把「迁移一个 kind」拆成 S2/S3/S4 三个阶段，是因为旧结构里两条执行路径平行、
需要批次内分流。新结构里**载体是 attempt 的一个字段**，所以：

- 「执行单元重构」（E1）做完后，两条 carrier 共用同一套 attempt 状态机；
- 「切换某个 kind 的 carrier」（E3）就是改一个开关值 + 补该 kind 的投影适配。

因此阶段收敛为四步。

| # | 阶段 | 交付 | 前置 | 验收出口 |
|---|---|---|---|---|
| E1 | `steward-execution-unit` | 迁移 0055 重写 + plan/attempt 模型 + 单链路调度 + fence 收敛到 2 处 + per-space 并发 | 本设计 | 全量检查全绿 + 迁移往返 + **行为等价**（全部 kind 仍 inproc） |
| E2 | `steward-sidecar-adapters` | sidecar `KindAdapter` 拆分，`executeJob` 零 `if kind` | E1 | agent 全量检查 + 槽位回归不变 |
| E3 | `steward-pi-carrier-terminology` | terminology 走 pi carrier | E2 | 差分等价 + egress 审计 + 崩溃收敛 + 写回栅栏回归 |
| E4 | `steward-pi-carrier-rest` | candidate/ranking/explanation 逐个走 pi carrier | E3 | 每 kind 差分 + 越权矩阵 |
| E5 | `steward-assist-cleanup` | 删除 inproc carrier 与开关（可选，稳定后） | E4 | 删除后全量检查 |

**E1 是唯一的架构性改动**，其余是增量。

---

## 1. E1 执行清单（执行单元重构）

### 1.1 迁移 `0055_steward_assist_execution_unit`（重写现有 0055）

- [ ] refusal guards（design §4.1 六条）**先于任何 DDL**。
- [ ] `steward_assist_batches` → `steward_assist_plans`：`ALTER TABLE ... RENAME TO`；
      删 `status`/`attempt`/`next_attempt_at`/`lease_owner`/`lease_until`/`error_code`/`updated_at`
      （**原生 `DROP COLUMN`，不重建表**）。
- [ ] `steward_model_calls`：加 `lease_owner`/`lease_until`/`next_attempt_at`/`carrier`；
      `batch_id` → `plan_id`（改名，非重建）；加 `ix_smc_due (status, next_attempt_at)`。
- [ ] `steward_runs`：`assist_batch_id`/`assist_kind`/`viewer_account_id` → `model_call_id`（UNIQUE）。
- [ ] `agent_runs`：kind 扩展 + `session_id` 可空 + `ck_agent_runs_scope_binding`（同 S1，已实现）。
- [ ] downgrade：refusal guard 先行（存在 `kind='steward'` 行即中止）；逆序恢复；
      **`steward_model_calls` 的既有 attempt 行在 downgrade 时状态归一化**（`in_flight` → `unknown`），
      否则恢复后的 batch 状态机无法表达。
- [ ] 迁移测试：空库、合法存量、六条 guard 各一条、`upgrade → downgrade → upgrade` 往返（临时 `DATA_DIR`）。

### 1.2 模型层

- [ ] `models/steward.py::StewardAssistPlan`（原 Batch 重命名 + 收窄）。
- [ ] `models/steward.py::StewardModelCall`：新增 4 列 + `plan_id` 改名 + carrier CHECK。
- [ ] `models/steward.py::StewardRun`：改绑 `model_call_id`。

### 1.3 调度层（`steward_assist.py` 重写核心段）

- [ ] `plan_for_job(...)`：登记 plan + 预留全部 attempt（`reserved`, `next_attempt_at=now`）。
      取代 `register_batch_for_job` + `_reserve_due_attempts` 的「先注册后预留」两段式。
- [ ] `lease_attempt(db, *, space_id, worker_id, ttl) -> LeasedAttempt | None`：
      按 space 计数在途 attempt → 选最早到期 → fence → 置 `in_flight`。**取代** `schedule_due_batch`。
- [ ] `settle_attempt(db, *, attempt_id, result, lease) -> str | None`：
      校验 lease → 计费/状态 → 封闭校验 → fence → CAS 应用。**取代** `_settle_attempt` + `_apply_batch`。
- [ ] `recover_stuck_attempts(db, *, now) -> int`：**取代** `recover_stuck_batches`。
- [ ] `_fence_check(db, *, plan, attempt) -> str | None`：改为「空间级 + 单 attempt 的 kind」，
      调用点收敛到**两处**（`lease_attempt`、`settle_attempt`）。
- [ ] `_apply_product(db, *, attempt, product, now) -> bool`：单 attempt 应用（从 `_apply_batch` 的
      for 循环里抽出），保留全部既有校验（候选原子 kind / 严格排列 / explanation schema / terminology 组校验）。
- [ ] `_budget_state` 改为按 `space_id`（预算仍 per-job 累计，但查询走 plan→job）。
- [ ] 删除 `execute_batch` / `schedule_due_batch` / `launch_batch` / `_execute_in_own_session` /
      `_reserve_due_attempts` / `_apply_batch` / `_finish_batch_after_attempts`。
- [ ] `_post_json` / `_post_json_async` 保留，包成 `InprocCarrier`。

### 1.4 载体抽象

- [ ] `services/steward_carrier.py`：`AssistCarrier` Protocol、`InprocCarrier`、
      `carrier_for(db, space_id, kind) -> AssistCarrier`（读 `STEWARD_ASSIST_<KIND>_CARRIER`）。
- [ ] `PiCarrier` 骨架：建 run + steward_run（S1 的 `lease_child_run` 逻辑移到这里），
      `execute` 抛 `NotImplementedError`（E3 实现）。

### 1.5 配置

- [ ] `STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`（默认 2，上界 8）。
- [ ] `STEWARD_ASSIST_<KIND>_CARRIER`（4 个，`inproc|pi`，默认 `inproc`，未知值 fail-closed）。
- [ ] 移除 `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（或保留为 deprecated alias 并 warn）。

### 1.6 内部协议

- [ ] `POST /internal/agent/steward/attempts/lease`（取代 `/steward/jobs/lease`）。
- [ ] `_settle_steward_run` 的 hook 改调 `settle_attempt`。
- [ ] `heartbeat_child_run` 改为续租 attempt lease（而非 batch）。
- [ ] `ContextOut` / 事件门禁 / context 守卫 / `_authorize_steward_run`（S1 已实现，改绑 `model_call_id`）。

### 1.7 E1 验收

- [ ] **行为等价**：四种 kind 全部走 inproc carrier，`steward_model_calls.run_id` 全 NULL，
      现有 steward 测试语义不变（`transport=` 注入点保留）。
- [ ] **fence 调用点 = 2**（`grep -c _fence_check` 断言）。
- [ ] **per-space 并发**：两个空间各有到期 attempt 时，两者**同时**可租（旧实现只能一个）。
- [ ] 全量检查：

```bash
cd backend && .venv/bin/python -m pytest -q && ruff check . && ruff format --check . && mypy app
TMP=$(mktemp -d) && DATA_DIR="$TMP" .venv/bin/python -m alembic upgrade head \
  && DATA_DIR="$TMP" .venv/bin/python -m alembic downgrade -1 \
  && DATA_DIR="$TMP" .venv/bin/python -m alembic upgrade head
```

---

## 2. E2 执行清单（sidecar adapter）

- [ ] `agent/src/adapters/kind.ts`：`KindAdapter` 接口 + `assistantAdapter` + `stewardAdapter`。
- [ ] `worker.ts::executeJob` 改为依赖 adapter：删 5 处 `if kind`。
- [ ] `session.ts` 的 `systemPrompt` / `cacheKey` / `toolNamesFor` 从 adapter 取。
- [ ] `events.ts` 的产物上报按 `adapter.emitsMessageEvents` 分派。
- [ ] 槽位模型不动（S1 已验证）。
- [ ] 回归：`worker-slots.test.ts` / `poll-scheduling.test.ts` 全绿；新增 adapter 单测。

**E2 验收**：`cd agent && npm run type-check && npm run lint && npm test && npm run build`。

---

## 3. E3 执行清单（terminology 走 pi carrier）

- [ ] `PiCarrier.execute` 实现：建 run + steward_run → 返回 grant 供 sidecar 租。
- [ ] `STEWARD_ASSIST_TERMINOLOGY_CARRIER=pi` 生效。
- [ ] `steward_terminology.PROMPT_VERSION` 改为引用 `steward_assist.STEWARD_PROMPT_VERSION`
      （跨层字面量单源），加回归断言「prompt 版本变化会改变 `request_hash_for()`」。
- [ ] `recover_stuck_attempts` 覆盖崩溃点⑤（child run 已建、sidecar 崩溃）。

**E3 验收**：

- [ ] 差分等价：同一输入两条 carrier 产出**结构等价**的 `output_json`。
- [ ] egress 审计 `target_id = child run id`，`error_class`/`retryable`/`sent` 齐全。
- [ ] 计费等价：`billed_tokens` 保守上界一致。
- [ ] 崩溃收敛：child run 卡住 → `unknown` 且无第二次出站。
- [ ] 撤权收敛：空间成员撤销后，在途 attempt 在**一个维护 tick 内**终态化。
- [ ] 孤儿断言：回退开关后一个租约周期内无 `leased/running` 残留。
- [ ] 写回栅栏回归全绿（开关 / policy_version / provider revision / 证据摘要 / 卡片 revision / lease）。

---

## 4. E4 执行清单（其余三类）

- [ ] candidate / ranking / explanation 逐个 `CARRIER=pi`。
- [ ] 每 kind 差分测试与越权矩阵。
- [ ] ranking 严格排列、candidate 原子 kind 围栏、explanation 槽位/证据围栏**全部保持**。

---

## 5. E5 执行清单（清理，稳定后）

- [ ] 删除 `InprocCarrier` 与 `STEWARD_ASSIST_<KIND>_CARRIER`。
- [ ] 删除 `steward_model_calls.run_id` 为 NULL 的兼容分支（保留历史行）。
- [ ] 评估收敛迁移（收紧 CHECK）——**若会破坏历史行则不收紧**（memory #399）。

---

## 6. 全局验证入口

```bash
cd backend && .venv/bin/python -m pytest -q && ruff check . && ruff format --check . && mypy app
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd frontend && npm run type-check && npm run lint && npm test && npm run build
cd system-admin-frontend && npm run type-check && npm run lint && npm test && npm run build
AGENT_SERVICE_SECRET=x SECRET_KEY=y ADMIN_JWT_AUDIENCE=a ADMIN_JWT_SECRET=b ADMIN_JWT_ISSUER=c \
  docker compose config --quiet
```

**生产发布前置**（memory #359 / #443）：写库脚本必须显式 `DATA_DIR` 指向隔离目录。

---

## 7. 评审门

| 门 | 时点 | 必须通过 |
|---|---|---|
| G1 | E1 完成 | 行为等价 + 迁移往返 + fence 调用点=2 + per-space 并发证明 |
| G2 | E2 完成 | agent 全量检查 + 槽位回归不变 |
| G3 | E3 完成 | 差分等价 + egress 审计 + 撤权收敛 + 孤儿断言 |
| G4 | E4 完成 | 每 kind 差分 + 越权矩阵 |

---

## 8. 回滚点

| 阶段 | 回滚动作 | 数据影响 |
|---|---|---|
| E1 后 | 无（架构改动，需 revert 提交） | 迁移不回退（`steward_runs` 空） |
| E2 后 | `FG_AGENT_ROLE=assistant` | 无 |
| E3 后 | `STEWARD_ASSIST_TERMINOLOGY_CARRIER=inproc` | 在途 attempt 收敛为 `unknown` |
| E4 后 | 逐 kind 关 carrier | 同上 |
