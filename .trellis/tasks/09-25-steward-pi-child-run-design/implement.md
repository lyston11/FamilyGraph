# Steward Pi child run 实施计划

> 前置：`prd.md` + `design.md` 经用户审阅通过。Q1–Q6 **已裁定**（`design.md` §16），本文件按裁定结果编写。
> 本文件是**后续实施子任务的执行计划**，不是本任务（设计任务）的执行计划。本任务自身在 §0 收尾。

---

## 0. 本任务（设计任务）的收尾

本任务只产出文档。完成条件：

- [ ] `prd.md` / `design.md` / `implement.md` 三份文档就位。
- [x] `design.md` §16 的 Q1–Q6 已获用户裁定（2026-09-25），裁定结果已回写 `design.md`（无 TBD）。
- [ ] `python3 ./.trellis/scripts/task.py validate .trellis/tasks/09-25-steward-pi-child-run-design` 通过。
- [ ] 确认仓库除本任务目录外无改动（`git status` 只应出现 `.trellis/tasks/09-25-steward-pi-child-run-design/`）。
- [ ] 提交并归档；归档说明中**显式记录**「08-26 原始设计 → 09-01 收窄 → 本任务恢复」的完整脉络，闭合 08-29 `notes.md` §6 那条从未执行的待办。
- [ ] 按需创建 Stage 1/2/3 子任务（`task.py create ... --parent`），并把依赖写进各子任务的 `prd.md`，不靠树位置隐含。

**验证命令**：

```bash
cd /Users/lyston/PycharmProjects/familygraph
python3 ./.trellis/scripts/task.py validate .trellis/tasks/09-25-steward-pi-child-run-design
git status --porcelain
```

---

## 1. 子任务拆分总览

依赖写在每个子任务的 `prd.md` 里；下表只做导航。

| # | 子任务（建议 slug） | 交付 | 前置 | 独立验收出口 |
|---|---|---|---|---|
| S1 | `steward-child-run-skeleton` | 迁移 0055 + 身份/协议/**sidecar 多槽改造**骨架，零行为变化 | 本设计（Q1–Q6 已裁定） | 现有全量检查全绿 + 迁移往返 + refusal guard 回归 + 多槽隔离回归 |
| S2 | `steward-child-run-terminology` | terminology 一类走 child run | S1 完成并归档 | 差分等价 + egress 审计 + 崩溃收敛 + 写回栅栏回归 |
| S3 | `steward-child-run-tools-design` | steward 只读工具集**设计**（§9.4） | S2 完成（或与 S2 并行，但实施不得早于 S2） | 设计评审通过 |
| S4 | `steward-child-run-remaining-assists` | candidate/ranking/explanation 逐类迁移 | S2 + S3 设计 | 每 kind 差分测试 + 越权矩阵 |
| S5 | `steward-child-run-cleanup` | 移除 in-process 路径与开关 | S4 全部打开且稳定 | 删除代码后全量检查 + 迁移收敛（如需要） |

**串行硬约束**：S1 → S2 →（S3 设计）→ S4 → S5。S3 的**设计**可与 S2 并行，但 S4 不得早于 S2 与 S3 设计同时完成。

**与既有任务的关系**：

- `09-11-steward-capability-followups`（P3）与 `09-11-steward-cross-space-discovery`（P3）**不被本设计阻塞**，但它们的实施前提在本设计落地后才具备（shared RAG 辅助需要 steward run 身份；跨空间发现需要稳定的授权形状）。**不得**在本设计的子任务里顺手实现它们。
- `09-11-steward-assist-execution`（已归档）建立的 `StewardAssistBatch`/`StewardModelCall` 状态机是本设计的**保留资产**，S2/S4 只改「谁执行 HTTP」，不改状态机语义。

---

## 2. S1 执行清单（骨架）

> 目标：把新路径**建成但不启用**。S1 结束时，四种 assist 的行为必须与今天逐字节等价。

### 2.1 迁移（`0055_steward_child_run`）

- [ ] 先写 refusal guards（`design.md` §4.1 五条），在任何 DDL 或版本移动之前执行。
- [ ] `agent_runs`：`kind` CHECK 扩展为 `IN ('assistant','steward')`；`session_id` 改 nullable；新增 `ck_agent_runs_scope_binding`。
- [ ] 保留 `uq_agent_runs_session_active`、`uq_agent_runs_job_id`、FK、`ix_agent_runs_session_id`。
- [ ] 新建 `steward_runs` 表（`design.md` §4.4）。
- [ ] `steward_model_calls` 加 `run_id`（`ON DELETE SET NULL`）+ `ix_smc_run`。
- [ ] downgrade：先跑 refusal guard（存在 `kind='steward'` 行即中止），再逆序恢复。
- [ ] 迁移测试：空库、合法存量、冲突数据（五条 guard 各一条）、`upgrade → downgrade → upgrade` 往返，**全部使用临时 `DATA_DIR`**。

**验证**：

```bash
cd backend
TMP=$(mktemp -d) && DATA_DIR="$TMP" .venv/bin/python -m alembic upgrade head
DATA_DIR="$TMP" .venv/bin/python -m alembic downgrade -1
DATA_DIR="$TMP" .venv/bin/python -m alembic upgrade head
.venv/bin/python -m pytest tests/test_steward_child_run_migration.py -q
```

### 2.2 运行时类型与身份

- [ ] `models/agent.py`：`RuntimeAgentKind = Literal["assistant","steward"]`；`RUNTIME_AGENT_KINDS` 扩展；`_AGENT_KIND_CHECK_SQL`（`agent_sessions`）**保持** `= 'assistant'`；`AgentRun.kind` CHECK 扩展；`AgentRun.session_id` 类型改 `int | None`。
- [ ] 新增 `models/steward.py::StewardRun`。
- [ ] `services/agent_execution.py`：`AssistantExecution` / `StewardExecution` / `Execution` union；`fence_assistant_execution` / `fence_steward_execution`（`design.md` §5.2 的检查集）。
- [ ] `services/agent_tokens.py`：per-kind `_RUN_REQUIRED_CLAIMS`；`issue_run_token` / `decode_run_token` 按 kind 校验。
- [ ] `agent_queue._validate_kind`：**只服务 assistant 队列**（保持 `RUNTIME_AGENT_KINDS` 不再直接可用——必须显式收窄为 `("assistant",)`，避免 steward 被误允许进 `agent_jobs`）。

> ⚠️ 这里有个**容易写错的点**：`RUNTIME_AGENT_KINDS` 扩展后，任何直接用它做「队列 kind 白名单」的地方都会**静默放开** steward。必须逐个审查 `agent_queue.py`、`agent_tools.py:282`、`agent_tokens.py` 三处，改成显式常量（`QUEUE_AGENT_KINDS = ("assistant",)` / per-kind 表）。

### 2.3 内部协议

- [ ] 新增 `POST /internal/agent/steward/jobs/lease`（`design.md` §6.2），`STEWARD_ENABLED` + `STEWARD_PI_RUNTIME_ENABLED` 双开关 503 门禁。
- [ ] `steward_assist.lease_child_run(db, leased_by, ttl)`：选 batch → 预留 attempt → 建 run + steward_run → 返回 grant（无网络）。
- [ ] `_authorize_run` 拆分为 `_authorize_assistant_run` / `_authorize_steward_run`。
- [ ] `heartbeat`：steward 分支在同一立即事务内同时续租 run 与 batch（`design.md` §11.2）。
- [ ] `ContextOut`：`session_id` / `account_id` 改可空（`design.md` §6.4）。
- [ ] `GET /runs/{id}/context`：steward 分支走 `context_builder.build(agent_kind="steward", run_id=...)`，`messages=[]`。
- [ ] `_validate_entry`：`run.kind == 'steward'` 时拒绝消息类事件（`design.md` §10.3）。
- [ ] `settle` 端点：steward 分支调 `steward_assist.settle_child_run`（**同事务**）。
- [ ] `agent_tools.check_scope`：按 kind 读对应 execution 的 allowlist。

### 2.4 context_builder 守卫改造

- [ ] `context_builder.py:171` 的「steward 不得有 run_id」**改为正向校验**：`run_id` 必须解析到 `steward_runs` 行且 space 一致，否则 422。
- [ ] 回归：伪造 steward run_id（不存在的 id、跨空间的 steward_run）→ 422；合法 steward_run → 通过。

### 2.5 隔离与可观测性

- [ ] `api/agent.py`：`_own_session_or_404` / `_own_run_or_404` 加 `agent_kind == 'assistant'` 显式条件。
- [ ] `admin_read_model.agent_runs`：`account_id` 可为 `None`；`AdminAgentRunOut.account_id: int | None`；`session_id: int | None`。
- [ ] `admin_agent_latency`：新增 `kind` 参数，**默认 `assistant`**（`design.md` §10.2）。
- [ ] `agent_queue.prune_finished`：加 `AgentRun.kind == 'assistant'` 过滤。

### 2.6 Sidecar（**Q1=A2：多槽并发改造，本阶段最大风险项**）

- [ ] `config.ts`：新增 `role: "assistant" | "steward" | "both"`（`FG_AGENT_ROLE`，缺失默认 `assistant`；**未知值 fail fast**）；新增 `maxConcurrentRuns`（`AGENT_MAX_CONCURRENT_RUNS`，默认 2）；新增 `stewardMaxConcurrentBatches`（`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`，默认 1）；两者加上界校验（与后端 `1..8` 区间一致）。
- [ ] `client.ts`：`leaseJob(kind)` 按 kind 选端点与 body；`getRunContext()` 的严格解码按 `agent_kind` 分支（`design.md` §6.4）。
- [ ] **`worker.ts` 单槽→多槽**（**照 `design.md` §7.8.3 的参考实现写**）：
  - [ ] 槽位类型：`type Slot = PendingSlot | ActiveRun`（均带 `kind`）；`slots: Map<string, Slot>`（真 run 用 `run_id` 作 key，占位用 `pending:${kind}:${n}`）。
  - [ ] `tryLeaseAndRun(kind = "assistant")`：先查 `inFlight(kind) >= slotsFor(kind)` → false；**先插占位再 await**；异常路径 `slots.delete(reservationKey)` 后 rethrow。
  - [ ] `pollLoop()`：遍历 `enabledKinds()`，每 kind `while (inFlight(kind) < slotsFor(kind))` 补满；内层失败 `break`（不 spin）；任一 kind 租到即 `didWork = true`。
  - [ ] `markLeaseLost` / `markCancelRequested`：`slots.get(runId)` + 判 `pending`（**§7.2.1：不改会导致跨槽取消被静默忽略**）。
  - [ ] `get isBusy()` → `this.slots.size > 0`；新增 `inFlight(kind)` 作为测试可观测点。
  - [ ] `executeJob` 内唯一必改行：`projection.agent_kind !== active.kind || job.agent_kind !== active.kind`。
  - [ ] `run.done = executeJob(...).catch(...).finally(...)`；finally 内 `if (this.slots.get(run_id) === run)` 后才删（**防止早完成者删掉晚占位的槽**）。
  - [ ] **槽位数**（`design.md` §7.3.1）：assistant = `AGENT_MAX_CONCURRENT_RUNS`（默认 2）；steward = `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（默认 1）。
  - [ ] **两侧漂移消除**（`design.md` §7.8.4）：`StewardLeaseOut` 新增 `max_concurrent` 字段；sidecar 取 `max(本地配置, 服务端广播)`，只上调不下调，不一致时 warn（含两数值、无业务数据）。
- [ ] projection 的 `agent_kind` 与 job 的 kind 不符立即抛错。
- [ ] `session.ts`：`systemPrompt` 按 job kind 选择；prompt cache key 按 kind 派生（assistant 保持 `fg-${account_id}-${session_id}` 不变）。
- [ ] 新增 `prompts/steward.ts`（骨架）+ 服务端 prompt 版本上报端点 + 两侧常量逐字断言。
- [ ] `tools.ts`：steward kind 下 allowlist 为空集（S1 无工具）；断言 assistant/steward 工具集不相交。
- [ ] **多槽新回归**（`design.md` §7.4，建议新建 `agent/test/worker-slots.test.ts`）：
  - [ ] ①**两槽并发**：用 `await Promise.all([worker.tryLeaseAndRun("assistant"), worker.tryLeaseAndRun("assistant")])` 真并发发起（不能串行 await，否则测不到预留）；断言两槽各跑一个 run，heartbeat/事件/结算按 `run_id` 互不串台。
  - [ ] ②**槽位预算隔离**：steward 槽被长调用占满时，assistant 仍能在自己的空槽上租到并完成（**这是 A2 相对 A1 的唯一价值证明**）。
  - [ ] ③**单槽故障不扩散**：任一槽的 lease 丢失/取消不影响另一槽（锁住 §7.2.1 的跨槽回调隔离）。
  - [ ] ④**槽位不泄漏**：`leaseJob()` 连续抛异常后 `inFlight(kind)` 回到 0，且后续仍能租到。
- [ ] 既有回归适配：`poll-scheduling.test.ts` **重写**（现有三个用例会失效，`design.md` §7.4 逐条说明）；`harness()` 的 spy 需接收 kind；`worker.integration.test.ts` 的 `makeAgentConfig` 补新字段（`tryLeaseAndRun()` 调用点本身无需改）。

### 2.7 部署

- [ ] `docker-compose.yml`：`agent` 服务新增 `FG_AGENT_ROLE`、`AGENT_MAX_CONCURRENT_RUNS`、`STEWARD_PI_RUNTIME_ENABLED`（**不新增容器**，`design.md` §7.5）；`STEWARD_PI_RUNTIME_ENABLED` 默认 `0`。
- [ ] `agent/Dockerfile` 无需改动（同镜像）。
- [ ] `docker compose config --quiet` 通过。

### 2.8 S1 验收

- [ ] 行为等价：四种 assist 仍走 in-process；`steward_model_calls.run_id` 全 NULL。
- [ ] 受控 E2E（唯一允许的新路径验证）：手工建 batch → `POST /internal/agent/steward/jobs/lease` → context → settle（**不接真实模型**，用 fake provider）。
- [ ] 全量检查：

```bash
cd backend && .venv/bin/python -m pytest -q && ruff check . && ruff format --check . && mypy app
cd frontend && npm run type-check && npm run lint && npm test && npm run build
cd system-admin-frontend && npm run type-check && npm run lint && npm test && npm run build
cd agent && npm run type-check && npm run lint && npm test && npm run build
```

- [ ] Compose 真实联调（`design.md` §14 顺序）。
- [ ] 安全回归矩阵（`design.md` §13「fence 泛化」行）：membership 撤销 / lease 过期 / 取消 / attempt 不匹配 / 跨空间 / allowlist 漂移，**两个 fence 函数各一组**。
- [ ] 分层判据回归（`design.md` §5.2.1）：撤权后 child run 在一个 tick 内收敛；父 job 终态后 child run 不再出站；**移除任一检查则回归失败**。

**S1 回退**：`FG_AGENT_ROLE=assistant`（退回单槽单 kind）；`STEWARD_PI_RUNTIME_ENABLED=0`。迁移不回退（向后兼容扩展，且 `steward_runs` 为空）。

---

## 3. S2 执行清单（terminology）

- [ ] `steward_assist` 抽出「发送」抽象：`_send_via_pi(run_id, payload)` vs 现有 `_post_json`。
- [ ] terminology 的 `_reserve_attempt` 改为创建 child run（而非直接发送）。
- [ ] `settle_child_run`：attempt 状态机 + 保守计费 + 封闭 schema 校验 + 写回栅栏 + CAS 应用（**语义不变**，只是触发点从「HTTP 返回」变成「settle 端点」）。
- [ ] `recover_stuck_batches` 增加崩溃点⑤（`design.md` §11.4）。
- [ ] 父 job 终态 → 置位活跃 child run 的 `cancel_requested`（`design.md` §11.3）。
- [ ] 开关 `STEWARD_ASSIST_TERMINOLOGY_VIA_PI`（默认 `0`）。

**S2 验收**：

- [ ] 差分等价：同一批输入两条路径产出结构等价的 `output_json`。
- [ ] egress 审计：`target_id = child run id`，`error_class`/`retryable`/`sent` 齐全。
- [ ] 预算等价：`billed_tokens` 保守上界一致。
- [ ] 崩溃收敛：child run 卡住 → `unknown` 且无第二次出站。
- [ ] 撤权收敛：空间成员撤销后，在途 child run 在**一个维护 tick 内**终态化（对应 09-19 的撤权不收敛教训）。
- [ ] 孤儿断言：回退开关后一个租约周期内 `steward_runs` 无 `leased/running` 残留。
- [ ] 写回栅栏回归全绿（开关 / policy_version / provider revision / 证据摘要 / 卡片 revision / lease 六项各一条）。

**S2 回退**：`STEWARD_ASSIST_TERMINOLOGY_VIA_PI=0`。

---

## 4. S3 执行清单（工具设计）

- [ ] 产出独立 `design.md`，覆盖 `design.md` §9.4 的四项前置产出。
- [ ] 明确空间级只读工具的授权形状（`design.md` §9.2 的评审点）。
- [ ] 越权矩阵的用例清单（跨空间 / 不可见节点 / 未授权 viewer / 输出上限 / `public_payload` 洁净）。
- [ ] **不写代码**。

---

## 5. S4 执行清单（其余三类）

- [ ] candidate / ranking / explanation 逐个迁移，各自独立开关。
- [ ] 每 kind 的差分测试与越权矩阵。
- [ ] ranking 的严格排列校验、candidate 的原子 kind 围栏、explanation 的槽位/证据围栏**全部保持**。
- [ ] 若 S3 设计了工具，则 candidate 可改用工具式探索；**必须**先证明「工具路径不产生比投影路径更宽的可见性」。

---

## 6. S5 执行清单（清理）

- [ ] 删除 `_post_json` / `_post_json_async` / `Transport` 注入点，以及 `_API_PATHS`（若 egress 路径不再需要）。
- [ ] 删除 `STEWARD_ASSIST_*_VIA_PI` 开关与 `STEWARD_PI_RUNTIME_ENABLED`（或降级为常开）。
- [ ] 删除 `steward_model_calls.run_id` 为 NULL 的兼容分支（保留历史行）。
- [ ] 评估是否需要收敛迁移（如收紧 CHECK）——**若收紧会破坏历史行，则不收紧**（memory #399）。
- [ ] 全量检查 + Compose 联调。

---

## 7. 全局验证入口

每个子任务收尾都必须跑：

```bash
# 后端
cd backend && .venv/bin/python -m pytest -q && ruff check . && ruff format --check . && mypy app

# 迁移（隔离 DATA_DIR，绝不触碰业务库）
TMP=$(mktemp -d) && DATA_DIR="$TMP" .venv/bin/python -m alembic upgrade head

# 三个前端/agent 包
cd ../frontend && npm run type-check && npm run lint && npm test && npm run build
cd ../system-admin-frontend && npm run type-check && npm run lint && npm test && npm run build
cd ../agent && npm run type-check && npm run lint && npm test && npm run build

# 真实链路（环境就绪时）
./scripts/dev-up.sh            # 或 dev-up-remote.sh
./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json
```

**生产发布前置**（memory #359 / #443）：写库脚本必须显式 `DATA_DIR` 指向隔离目录；发布前按 `design.md` §14 的顺序执行，并核对真实运行环境（systemd 作用域、生效配置来源、端到端可见结果），不以任务归档状态推断生产结论。

---

## 8. 评审门

| 门 | 时点 | 必须通过 |
|---|---|---|
| G0 | 本设计完成后 | 用户裁定 Q1–Q6（**已完成 2026-09-25**）；`design.md` 无 TBD |
| G1 | S1 完成 | 行为等价断言 + 迁移往返 + refusal guard + 两个 fence 的独立安全矩阵 |
| G2 | S2 完成 | 差分等价 + egress 审计 + 撤权收敛 + 孤儿断言 |
| G3 | S3 设计完成 | 空间级工具授权形状评审通过 |
| G4 | S4 完成 | 每 kind 差分 + 越权矩阵 |
| G5 | S5 完成 | 全量检查 + Compose 联调 + 生产发布核对 |

**G0 未通过不得 `task.py start` 任何实施子任务。**

---

## 9. 回滚点

| 阶段 | 回滚动作 | 数据影响 |
|---|---|---|
| S1 后 | `FG_AGENT_ROLE=assistant`，`STEWARD_PI_RUNTIME_ENABLED=0` | 无（`steward_runs` 为空） |
| S2 后 | `STEWARD_ASSIST_TERMINOLOGY_VIA_PI=0` | 在途 child run 收敛为 `cancelled`/`unknown`；`steward_model_calls` 保留 `run_id` 供追溯 |
| S4 后 | 逐 kind 关开关 | 同上 |
| S5 后 | **无开关回退**（代码已删） | 需要 revert 提交；这是 S5 的风险点，必须在前四个阶段稳定后再做 |
| 迁移 0055 | **不回退**（downgrade 在有 steward run 行时按设计中止） | 刻意的：不静默删除 child run 证据 |
