# Steward Pi child run 合同（S1 骨架 + E1 执行单元，2026-09-25）

> 适用任务：`09-25-steward-child-run-skeleton`（S1 骨架）、`09-25-steward-execution-unit`（E1 执行单元）；
> 父设计 `09-25-steward-pi-child-run-design`。
> 本文只定义一个可独立适用的合同：**Steward 的模型辅助由受限 Pi child run 执行**，
> 与 Assistant 共享执行合同但**不共享队列**。

## 1. Scope / Trigger

改动以下任一处前必读：`agent_runs` 的 kind 语义、`steward_model_calls` 的执行列、
`/internal/agent/steward/*`、`agent_execution.py` 的两个 fence、`agent_tokens` 的 run claims、
sidecar 的槽位模型、`steward_assist` 的 `lease_attempt/settle_attempt/open_child_run/heartbeat_child_run`。

## 2. 队列红线（最容易被破坏的一条）

`RUNTIME_AGENT_KINDS` 是「执行记录可表达的 kind」全集，**不是队列白名单**。三处必须显式区分：

```python
RUNTIME_AGENT_KINDS = ("assistant", "steward")   # agent_runs.kind 可取值
QUEUE_AGENT_KINDS   = ("assistant",)             # agent_jobs 只能这些
SESSION_AGENT_KINDS = ("assistant",)             # agent_sessions 只能这些
```

- `agent_queue._validate_kind` 用 `QUEUE_AGENT_KINDS`；`agent_tools`/`agent_tokens` 用 per-kind 表。
- 直接用 `RUNTIME_AGENT_KINDS` 做队列校验会**静默放开 steward 进 `agent_jobs`**，重建 09-01
  已消除的第二队列。回归：`test_agent_execution_fence.py::test_steward_job_cannot_enter_the_generic_queue`。
- `agent_sessions` / `agent_jobs` 的 CHECK 保持 `= 'assistant'`；不恢复历史
  `uq_agent_jobs_space_active`。

## 3. DB 强制不变量（迁移 0055）

- `agent_runs.kind IN ('assistant','steward')`；`session_id` 可空。
- `ck_agent_runs_scope_binding`：`assistant` 必须有 session；`steward` 必须**无 session 且无 job**。
  这是 DB 拒绝的，不只是服务层约定。
- `steward_model_calls` **就是执行单元**（E1，迁移 `0055_steward_assist_execution_unit`）：
  `lease_owner`/`lease_until`/`next_attempt_at`/`carrier` 在 attempt 上，不在 plan 上。
  `plan_id` 指向 `steward_assist_plans`（原 `steward_assist_batches`，收窄为不可变工作快照，无执行状态）。
  `carrier CHECK IN ('inproc','pi')`；`INDEX ix_smc_due (status, next_attempt_at)` 供租约选行。
  S5 起新行恒为 `'pi'`；CHECK 不收紧、历史 `'inproc'` 行不回写（memory #399）。
- **不存在 `steward_runs` 窄表**：child run 与 attempt 的绑定就是
  `steward_model_calls.run_id`，且它是 **UNIQUE**（`ON DELETE SET NULL`）：一个 child run ↔ 恰好一行
  attempt，否则结算无法判断结果属于哪一行。`job_id`/`assist_kind`/`viewer_account_id` 已在 attempt 上，
  反查是一次索引读，不需要第二张表同步。账本比执行记录活得久（prompt digest/计费可追溯）。
- `context_builds.account_id` 可空 + `ck_context_builds_account_binding`（assistant 非空、
  steward 为空）。**父设计「无需改 context_builds」是错的**：该列原本 NOT NULL，空间级
  steward 投影无法记录。
- 迁移顺序硬约束：**refusal guards 先于任何 DDL**；downgrade 同样先跑守卫，且必须前置复现
  祖先（0051/0053）自己的拒绝判定，否则深层降级会留下半降级 schema。
- downgrade 用原生 `ALTER TABLE ... DROP COLUMN`，**不要重建表**：重建会让 SQLAlchemy 的
  批量反射重新推导约束名，破坏更早迁移（0044）的 `drop_constraint`。

## 4. 身份与授权（两个独立 fence）

```python
ExecutionIdentity  # assistant：账号锚定（run/job/session/account + 成员资格）
StewardExecution   # steward：空间锚定，**故意没有 account_id 字段**
fence_assistant_execution(db, identity, ...)   # 完整检查集
fence_steward_execution(db, identity, ...)     # 另一套完整检查集
fence_execution(db, identity, ...)             # 只按**类型**分派，不做判定
```

- **不要合并成一个按 kind 分支的函数**：漏掉一支里的一个检查就是静默后门，且一套矩阵无法
  同时证明两支。`StewardExecution` 没有 `account_id`，所以写不出「按账号授权 steward」的代码。
- steward 的授权根是**父 `StewardJob`**：父 job 必须 active；空间必须存在；`viewer_account_id`
  非空时该账号的 user 必须仍是 active 成员；attempt 绑定时 attempt 必须 leased（`in_flight`）。
- 分层判据的**残留风险**（已裁定接受）：空间级处理不因单个用户撤权而停止，撤权收敛上界是
  一个维护 tick 而非下一次内部请求。回归必须证明撤权在一个 tick 内收敛。
- 回归矩阵：`tests/test_agent_execution_fence.py`（39 用例，两条 fence 各自完整）。该文件用
  **变异测试**推导——逐条删检查确认有用例失败；结构性不可证伪的检查（被 immutability trigger
  或 CHECK 挡住）在文件里**逐条注明原因**，不留静默未测的行。

## 5. Run token（per-kind claims）

```python
_RUN_REQUIRED_CLAIMS_BY_KIND = {
  "assistant": (run_id, job_id, attempt, agent_kind, account_id, space_id, tool_allowlist),
  "steward":   (run_id, job_id, attempt, agent_kind, space_id, tool_allowlist),
}
```

- assistant **必须**给 `account_id` 且**不得**给 steward 专属字段；steward **必须不**给
  `account_id`。少给或多给都 fail-closed（宁可签不出来，也不签语义含混的 token）。
- steward 可选 claims：`steward_attempt_id`（= `StewardModelCall.id`）、`viewer_account_id`
  （缺失或正整数，其他类型拒绝）。旧名 `steward_batch_id` 对应已被 E1 移除的批次。
- `decode_run_token` 先按 kind 取必含集再校验；顺序反了会用一个宽集合放过语义错配。

## 6. Internal 协议

| 端点 | steward 行为 |
|---|---|
| `POST /internal/agent/steward/attempts/lease` | **独立端点**，`STEWARD_ENABLED` **AND** `STEWARD_PI_RUNTIME_ENABLED` 双开关 503；无可租 204。`space_id` **可选**：省略时服务端选一个有容量且有到期工作的空间（sidecar 不知道空间拓扑）——**不回退到全库 1**，预算仍是 per-space 且会跳过已满的空间 |
| `POST /internal/agent/jobs/lease` | **保持 `kind="assistant"`**，不放开 |
| `POST /jobs/{id}/heartbeat` | steward 分支**同一立即事务**内同时续 run 与 attempt lease。**`{id}` 取自租约响应的 `job_id`**（= 父 `StewardJob.id`）：sidecar 对两个 kind 都解 `String(raw["job_id"])` 并据此拼心跳 URL，所以 `StewardLeaseOut` **必须**同时携带 `job_id` 与 `steward_job_id`（同值，前者是协议统一字段，后者是授权根命名）。缺 `job_id` 会让 URL 变成 `/jobs/undefined/heartbeat`，被 token-scope 检查 403 拒绝，sidecar 按失租 abort——任何超过一个租约周期的调用都会因此失败（回归：`test_steward_child_run_acceptance.py::test_the_steward_lease_carries_the_job_id_the_sidecar_heartbeats`、`agent/test/client.test.ts`、`agent/test/worker.integration.test.ts`） |
| `GET /runs/{id}/context` | `session_id`/`account_id` 为 null、`messages: []`、带 `steward_prompt_version` 与 `steward_instructions` |
| `POST /runs/{id}/events/append` | `run.kind == 'steward'` 时**拒绝消息类事件**（422） |
| `POST /runs/{id}/provider/*` | **两个 kind 都可达**（唯一 egress）；授权按 token 的 kind 分派，provider 解析带 run 自己的 kind |
| `POST /runs/{id}/settle` | steward 分支经 `settle_run(on_settled=...)` **同事务**结算 attempt（phase 1）；产物写回是 phase 2，在 run 事务提交后（见 §12） |
| `POST /runs/{id}/tools/{tool}/execute` | 复用；allowlist 按 kind |

- **为什么独立端点而不是放开 `LeaseRequest.kind`**：「哪个容器能租哪类作业」必须是**路由级**
  约束，否则任何持 service token 的调用者（含被入侵的 assistant 容器）都能消费 steward 队列。
- **settle 必须同事务**：分两次提交会在崩溃时留下「run succeeded / attempt 仍 in_flight」，
  即 R2 禁止的双终态。实现点是 `agent_queue.settle_run` 的 `on_settled` 钩子。
- 双开关是刻意的：「引擎开启」与「执行载体切换」是两个独立发布决策，必须能各自回退。
- `_authorize_assistant_run` 入口断言 `agent_kind == 'assistant'`，反之亦然。
- `_reject_user_jwt` 必须**先于** kind 探测：否则 user JWT 会从 403 变成 401。

## 6.1 Steward 只读工具合同（S3）

Steward child run 的 `tool_allowlist` 由后端 `agent_tools.REGISTRY` 按
`required_kind="steward"` 生成，并同时写入 run、token claim、context projection；sidecar
`KindAdapter.toolNames()` 必须返回同一组 canonical names。工具执行先过 user-JWT rejection、run token、
`StewardExecution` fence、allowlist、版本和闭合 schema，再进入 `steward_tools.py` 的纯读取分派。

工具集合固定为：

- `familygraph.steward.get_space_snapshot`：当前空间已发布投影的版本、完成统计和 policy version；输入 `{}`。
- `familygraph.steward.list_space_nodes`：当前空间有限稳定节点摘要；只接受 `cursor` / `limit`。
- `familygraph.steward.get_viewer_target`：带 `viewer_account_id` 时读取该 viewer 的 ready 目标投影。
- `familygraph.steward.get_viewer_term`：带 viewer 且 root 与 token viewer 一致时读取 term projection。
- `familygraph.steward.get_evidence`：attempt 仍为 `in_flight` 且 target 属于已发布 view 时读取结构化证据 ID。
- `familygraph.steward.get_relationship_path`：读取当前空间已发布且已确认的路径；带 viewer 时 root 必须是该 viewer。

除业务查询字段外，schema 使用 `additionalProperties=false`，拒绝 `space_id`、`account_id`、
`viewer_account_id`、`run_id`、`attempt_id`、provider 和任意权限字段。viewer 缺失、撤权、未发布、
跨空间、revision/attempt 绑定无法证明时统一 fail closed；不可见目标不得返回目标身份、状态或路径规模。
工具只读现有 Steward publication/view/term/evidence projection，不读取 prompt、provider、Memory/RAG
原文，也不产生领域写入；结果经统一 JSON 上限和 output policy guard，审计不含原始输入。

## 7. sidecar 槽位模型

- `slots: Map<string, Slot>`，真 run 用 `run_id` 作 key，占位用 `pending:<kind>:<n>`。
- **先预留槽位再 `await leaseJob()`**：`leaseJob` 是挂起点，且 SQLite 写锁保证两个并发租约拿到
  **不同** job，所以不预留就会租到无槽可跑的 job。占位必须被预算计入，异常路径必须释放。
- `tryLeaseAndRun(kind)`（测试 seam，await 完成）与 `leaseIntoSlot(kind)`（pollLoop 用，不等）
  **是两个东西**：pollLoop 里 await 会把槽位串行化，等于撤销这次改造。
- `markLeaseLost` / `markCancelRequested` 必须按 `run_id` 查表；保留「当前 run」比较会**静默丢弃**
  除一个之外所有槽位的取消信号。
- 槽位预算**按 kind 独立**：steward 长调用不得占用 assistant 槽位（这是选「改现有 sidecar」
  而非新增容器的唯一价值证明）。
- `toolNamesFor(kind)`：Assistant 与 Steward 使用两个显式、互斥的集合；Steward 只注册
  `familygraph.steward.get_space_snapshot`、`list_space_nodes`、`get_viewer_target`、
  `get_viewer_term`、`get_evidence`、`get_relationship_path` 六个只读投影工具，
  不通过 Assistant 集合做差集，也不继承 echo/probe/Web/写入工具。

## 8. prompt 文本与版本（跨层字面量）

**prompt 文本由服务端拥有**（E2/E3 修正；S5 后是唯一事实）。sidecar 不持有 steward system prompt：

- 服务端在 context 投影里下发 `steward_instructions`（= `steward_assist._PROMPTS[kind]`），
  sidecar 把它作为 system message 发送；
- `prompt_digest` 是 `sha256(f"{instructions}\n{user_content}")`，覆盖的正是**实际发送**的文本；
- 只发一段通用的 steward prompt 会让模型被问一个与 digest 描述不同的问题——
  对 candidate 这类是承重的：方向语义与矛盾规则就住在那段文本里，输出校验器是第二道防线而非替代品。
- 回归：`test_steward_child_run_acceptance.py::test_both_carriers_send_the_same_prompt_text`
  用投影里拿到的东西**重建 digest**，因此「发送的就是 digest 描述的那段」是可检验的而不是声明。
- sidecar **不得**保留本地 steward prompt 作 fallback（S1 曾有）：它会被发送**代替**服务端文本，
  反而掩盖差异。`agent/src/prompts/steward.ts` 只导出 `STEWARD_PROMPT_VERSION`。
- `steward_assist.STEWARD_PROMPT_VERSION` 是锚点，经 context 投影下发给 sidecar；
- sidecar `STEWARD_PROMPT_VERSION` 不匹配时 **fail-closed**（不建 session、不发模型请求）；
- 两侧**逐字断言**该字面量（`test_agent_execution_fence.py` 与 `agent/test/worker-slots.test.ts`），
  单侧改名必须失败测试，而不是运行时拒绝所有 steward run。
- prompt cache key 按 kind 派生：assistant 保持 `fg-${account_id}-${session_id}`；
  steward 用 `fg-steward-${space_id}`（assistant 公式对 steward 会得出 `fg-null-null`，
  让不相关空间共享同一上游缓存前缀）。

## 8.1 sidecar 的 kind 差异只允许住在一个适配器里（E2）

`agent/src/adapters/kind.ts` 的 `KindAdapter` 是**唯一**知道两个 kind 差异的地方：
`systemPrompt` / `modelPrompt` / `toolNames` / `slotBudget` / `cacheKey` / `emptyToolAllowlistIsInvalid` /
`verifyProjection` / `adoptsServerConcurrency` / `reportsProductOnSettle` / `extractProduct` /
`leaseRequest` / `decodeLease`。`executeJob` 与 `buildRunSession` 只依赖接口。

- **结构性断言**：`agent/test/adapters-kind.test.ts` 读 `worker.ts`/`session.ts`/`client.ts`/`events.ts`，
  出现新的 `kind === "assistant"|"steward"` 比较即失败（变异测试验证）。
- **租约路由由适配器给出**：steward 打 `/internal/agent/steward/attempts/lease`，且 body **不含** `space_id`
  ——sidecar 不知道空间拓扑，由服务端选一个有容量且有到期工作的空间（见 §6）。
- **产物路由由适配器声明**：`reportsProductOnSettle`。child run 拒绝消息类事件，所以 settle 是产物的
  唯一路径；不携带会让服务端在 `_settle_attempt` 的 `assert text is not None` 处失败。

## 8.2 网关（唯一 egress）对两个 kind 都必须可达（E3）

`/internal/agent/runs/{id}/provider/*` 是**唯一** egress，因此 steward child run 必须能到达它：

- 授权按 token 的 kind 分派（`_authorize_provider_run`），两套检查集仍然独立；
  assistant token 打 steward run 得 **404**（该身份看不到这个 run），不是 500。
- **`resolve_runtime` 与 `resolve_for_run` 都必须带 run 自己的 kind**：两个 kind 有独立的空间设置，
  按 assistant 解析 steward run 会读到不存在的配置，报「provider 不可用」或「云被禁止」——
  一个**看似合理**的答案而不是错误，所以 review 抓不到。结构性断言见
  `test_no_remaining_provider_resolution_defaults_to_the_assistant_kind`（变异测试验证）。
- 经网关是 child run 唯一能拿到 `agent_provider_egress` 审计的路径（进程内载体直连 provider，无审计）；
  审计行以 **child run id** 为 `target_id`。

## 9. 隔离与可观测性

- 浏览器面：`_own_run_or_404` / `_own_session_or_404` **显式**断言 `kind == 'assistant'`，
  不依赖 `db.get(AgentSession, None)` 返回 None 的巧合。
- `agent_queue.prune_finished` 只删 assistant：steward 的保留策略归 `steward_gc`。
- admin 读模型用 **OUTER join**（inner join 会丢掉全部 child run）；steward 的 space 取自父 job；
  **不暴露** `viewer_account_id` 或 prompt 内容。
- `GET /admin-api/v1/agent/latency` 新增 `kind` 参数，**默认 assistant**：两套种群（会话式 vs
  空间级）的耗时分布不可混算，混算会同时污染两者的中位数。

## 10. 部署

`agent` 服务新增 `FG_AGENT_ROLE`（缺失默认 `assistant`，未知值 **fail fast**）、
`AGENT_MAX_CONCURRENT_RUNS`（默认 2）、`STEWARD_PI_RUNTIME_ENABLED`（默认 0）。
**不新增容器/端口/卷。**

E1 把并发作用域从「全库批次」改为「每空间 attempt」：后端用
`STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`（默认 2，上界 8），旧值
`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（全库 1）已废弃——它让 20 个空间同时只能有一个在跑。
sidecar 侧对应 `STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`（同名同默认）：服务端决定放行几个
attempt，sidecar 决定能跑几个，不一致会让 attempt「已预留但无人执行」。sidecar 另从 lease 响应读取
服务端当前值作纠错（只上调不下调），但**广播是纠错机制，不替代两侧配置**。

部署顺序：backend 先于 sidecar（后端对未注册事件类型返回 422）。回退顺序相反。
回退：`FG_AGENT_ROLE=assistant` + `STEWARD_PI_RUNTIME_ENABLED=0`；迁移不回退。

## 11. Required validation

```bash
cd backend && .venv/bin/python -m pytest -q && ruff check . && ruff format --check . && mypy app
cd agent && npm run type-check && npm run lint && npm test && npm run build
# 迁移往返（隔离 DATA_DIR）
TMP=$(mktemp -d) && DATA_DIR="$TMP" .venv/bin/python -m alembic upgrade head \
  && DATA_DIR="$TMP" .venv/bin/python -m alembic downgrade 0054_seed_household_roster_fix \
  && DATA_DIR="$TMP" .venv/bin/python -m alembic upgrade head
AGENT_SERVICE_SECRET=x SECRET_KEY=y ADMIN_JWT_AUDIENCE=a ADMIN_JWT_SECRET=b ADMIN_JWT_ISSUER=c \
  docker compose config --quiet
```

关键回归入口：`tests/test_steward_execution_unit_migration.py`（E1 迁移守卫/形状/往返）、
`tests/test_steward_child_run_migration.py`（S1 守卫/形状/往返）、
`tests/test_agent_execution_fence.py`（两条 fence 矩阵）、`tests/test_steward_child_run_acceptance.py`
（行为等价 + 受控 E2E + 发送门与 fence 调用点断言）、
`tests/test_steward_pi_carrier_terminology.py`（网关可达 + egress 审计 + 崩溃收敛 + 孤立租约收敛）、
`tests/test_steward_pi_carrier_remaining_kinds.py`（candidate/ranking/explanation 的每 kind 围栏）、
`tests/steward_pi_harness.py`（驱动一次 Pi attempt 的测试辅助：lease → context → settle，
`respond` 扮演模型，`between` 是写回栅栏的观察窗口）、
`agent/test/adapters-kind.test.ts`（适配器每条差异 + 无残留 kind 分支）、
`agent/test/client.test.ts`（租约路径/请求体与 settle 产物形状）、
`agent/test/worker-slots.test.ts`（槽位隔离四条）、`agent/test/poll-scheduling.test.ts`（调度与槽位预算）。

## 12. 执行单元不变量（违反会静默停摆）

- **发送门唯一且必须 sweep**：`_fence_check` 只允许三个调用点——`lease_attempt`（发送门）、
  `record_attempt_outcome`（写回门；`settle_attempt` 为不持锁的调用方包装它）、
  `recover_stuck_attempts`（补做被中断的写回）；AST 断言在
  `test_steward_child_run_acceptance.py::test_the_write_back_fence_has_exactly_two_call_sites`。
  发送门遇到被栅栏拦下的 attempt 必须**当场退休为 `skipped` + 安全原因码并继续看下一个候选**，
  不得直接返回：没有别的地方会再租它，留在 `reserved` 会让本空间永远看起来有活干，
  且 `plan_error_code` 永远为空（原因码只有栅栏跑过才存在）。
- **发送门必须按 carrier 选行**：attempt 只能由它声明的载体执行。S5 后新行恒为 `pi`，
  选行条件仍在（历史 `inproc` 行因此永不被租），但进程内调度泵已删除，不再有「泵租到
  `pi` attempt 后卡到租约过期」的路径。
- **plan 无状态**：`steward_assist_plans` 是不可变快照。「这批工作结果如何」一律由
  `plan_outcome` / `plan_error_code` 从 attempt 派生，派生规则逐条复现旧 `_apply_batch` 的终态码。
- **HTTP 不在写事务内**：发送所需的 runtime/投影/输出上界在租约事务内读出并放进 grant，
  出事务后才发送；载体 `execute` 不读不写数据库。
- **结算分两阶段，且第一阶段必须能在别人的事务里跑**（E3）：
  - phase 1 = `record_attempt_outcome`（**调用方持锁**）：校验租约 → 计费/状态 → 封闭校验 →
    写回栅栏。Pi 路径在 `settle_run` 的 `on_settled` 里调它，因为 run 终态与 attempt 结果
    必须原子可见（否则留下「run succeeded / attempt 仍 in_flight」的双终态）。
    `_immediate_tx` **拒绝嵌套**，所以 hook 不能调会自己开事务的 `settle_attempt`。
  - phase 2 = `apply_settled_attempt`（**自有事务**，幂等，`applied_at` 为门）：应用产物。
    在 run 事务提交后调用；失败则产物已持久化但未应用，正是崩溃点④，由
    `recover_stuck_attempts` 补做。合并两阶段会让写回失败连已付费的模型答案一起回滚。
  - phase 1 内的读必须在锁内（`Session.get` 会 autobegin，先读后锁会让函数无法从刚提交的
    调用方使用）。
- **child run 必须能自己收敛**：`recover_stuck_child_runs` 不得只置 `cancel_requested` 等别人裁决
  ——`agent_queue.reaper_pass` 选 `AgentJob`，而 steward run 的 `job_id` 恒为 NULL，没有任何一方
  会写终态。它在本函数内写 `expired`（不是 `cancelled`：没人请求过取消）。

## 13. Wrong vs Correct

### Wrong

```python
# 用「执行记录可表达的 kind」做队列校验：静默放开 steward 进通用队列。
if kind not in RUNTIME_AGENT_KINDS:
    raise_api_error(422, AGENT_KIND_UNSUPPORTED, "unsupported kind")
```

```ts
// 保留单例比较：第一个槽取消后，其他槽的取消被静默忽略。
if (this.active?.job.run_id === runId && !this.active.cancelRequested) { ... }
```

```python
# 分两次提交：崩溃后留下「run succeeded / attempt in_flight」。
settle_run(db, run, status=body.status)
steward_assist.settle_child_run(db, run, status=body.status)
```

```python
# hook 里调会自己开事务的包装函数：_immediate_tx 拒绝嵌套，settle 直接报错。
settle_run(db, run, status=...,
           on_settled=lambda s, r: steward_assist.settle_attempt(s, attempt_id=...))
```

```python
# 网关用默认 kind 解析 provider：steward run 读到 assistant 的空间设置，
# 报「云被禁止」——一个看似合理的答案，而不是错误，review 抓不到。
runtime = agent_provider.resolve_runtime(db, space_id, run=run)
resolution = agent_provider.resolve_for_run(db, run, space_id)
```

```python
# 发送门只取一行：被栅栏拦下的 attempt 永不退休，本空间永远停在 reserved。
attempt = db.scalar(select(StewardModelCall).where(...).limit(1))
if _fence_check(db, plan, attempt, attempt.assist_kind) is not None:
    return None        # 兄弟 attempt 也被一并堵住，且 plan_error_code 永远为空
```

### Correct

```python
if kind not in QUEUE_AGENT_KINDS:      # 队列白名单与 runtime kind 是两个集合
    raise_api_error(422, AGENT_KIND_UNSUPPORTED, "unsupported kind")
```

```ts
const slot = this.slots.get(runId);     // 按 id 定位，槽位间互不影响
if (slot !== undefined && !slot.pending && !slot.cancelRequested) { ... }
```

```python
settle_run(db, run, status=body.status, execution=identity,
           on_settled=lambda s, r: steward_assist.record_attempt_outcome(
               s, attempt_id=attempt_id, status=body.status, lease_owner=owner))
# phase 2 在 run 事务提交后：
steward_assist.apply_settled_attempt(db, attempt_id=attempt_id)
```

```python
# 解析带 run 自己的 kind：两个 kind 有独立的空间设置。
runtime = agent_provider.resolve_runtime(db, space_id, run=run, agent_kind=run.kind)
resolution = agent_provider.resolve_for_run(db, run, space_id, run.kind)
```

```python
for attempt in candidates:                    # 发送门：退休并继续，而不是放弃
    reason = _fence_check(db, plan, attempt, attempt.assist_kind)
    if reason is not None:
        attempt.status = "skipped"; attempt.error_code = reason
        continue
    break
```

## 14. 工具执行准入（09-30：连接池/工作线程饥饿）

**触发**：改动 `/internal/agent/runs/{id}/tools/{name}/execute` 的并发行为、连接池参数，
或任何「把同步 DB 工作放进工作线程」的端点前必读。

**缺陷（已受控复现）**：连接池上限（`app.db.POOL_MAX_CONNECTIONS`=15）**小于** AnyIO
工作线程上限（40）。每个工具请求都要写库（fence 写锁 → 准入 CAS → 幂等占位 → 审计），
所以每个在途工具占 1 条连接。池被占满后，等待连接的请求会在**工作线程内**阻塞最长
`pool_timeout`(30s)；40 个这样的请求耗尽全部工作线程，**心跳/lease/health 一起拿不到
执行机会**（实测：40 请求全部耗时精确等于 30.0s，期间 `anyio borrowed/total` 持续 40/40）。

**修复合同**：

- `execute_tool` 是 `async def`：**先在事件循环上等名额**（`asyncio.Semaphore`），
  等待期间不占工作线程、不占连接；拿到名额后才 `anyio.to_thread.run_sync` 进工作线程。
- 工具体用**自己的 Session**（`SessionLocal`），在工作线程内创建、commit、`finally` 关闭。
  **禁止**跨线程复用 `get_db` 的请求级 Session。
- 名额由 **done-callback** 释放（`asyncio.shield` + `add_done_callback`）：请求被取消时
  立即返回，但名额要等 worker **真正结束**才归还——提前放行会让取消路径绕过并发上限。
- 名额只覆盖工具执行。**心跳、lease、settle、context、health 一律不取名额。**
- `AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS`（默认 8）由 `config.ensure_ready` 强制
  `1 <= value <= POOL_MAX_CONNECTIONS - 1`：名额达到池上限时工具可把池占满，缺陷重现。
- 授权、fence 检查集、准入 CAS、幂等、审计语义**逐字不变**；排队后进入执行仍在 fence 内
  重新验证身份、租约与权限。
- 等待超阈值（1s）记 `stage=tool_admission_wait` 结构化日志：路由模板、run_id、时长、
  活跃/等待计数、上限。**不得**输出 payload/凭据/SQL 参数，**不得**把总响应时间说成
  「线程池耗尽」——只报实际测量的阶段。

**诊断陷阱**：不要用「日志唯一秒数」判断 API 是否静默——会漏计。用**请求计数**
（`grep -cE '"(POST|GET) /'`）复核：修复前 3 个窗口 0 请求（真静默），修复后同长度
窗口 1146 个 heartbeat/lease 被服务。另外，steward run 的 `turn` 间隔可达 180s+ 而
**不是**服务端饥饿——那是上游模型响应慢；判断依据是该窗口内是否有请求被服务。

**回归**：`tests/test_agent_tool_admission.py`（工具突发下心跳预算内成功 + 工作线程占用
≤ 名额 + 批次全部完成 + 取消不泄漏名额 + 名额上界校验）。变异验证：名额设为 1000
（等效无准入）时心跳断言必须失败。

## 15. 流式转发不得在事件循环上做同步 DB（09-30）

**触发**：改动 `provider_proxy.passthrough_with_audit`、per-chunk 取消复核，
或任何「async generator 里做同步数据库访问」的代码前必读。

**缺陷（已受控复现）**：`passthrough_with_audit` 每个 chunk 在**事件循环**上调用同步的
`_refresh_run_gate`（`db.rollback()` + `db.get()`）。连接池被占满时该调用在 QueuePool
里等待 `pool_timeout`（30s），**阻塞整个事件循环**——所有端点（含 `/api/health`、心跳、
lease）一起静默。实测：

```
池耗尽 + 事件循环上调用 -> 30.0s；心跳协程延迟 30,055ms
3 个 chunk -> 90.0s 全进程停顿（观测到的停滞为 96–111s = 3–4 chunk）
```

同一代码还**跨 chunk 持有连接**：请求级 Session 的 `db.get()` 开启事务，直到下一 chunk
才 rollback，实测整个流期间 `checkedout == 1`——100s 的流钉住 15 条连接中的 1 条 100 秒。

**合同**：

- per-chunk 的同步 DB 复核必须 `anyio.to_thread.run_sync` 进**工作线程**，不得在事件循环上；
- 该复核使用**独立的短生命周期 Session**（每次检查后立即归还连接），不得复用请求级 Session；
- 检查函数、判定条件与 `ProviderProxyError`（取消/失租）语义不变——`run_cancelled`
  分类与「取消是服务端权威裁决」的合同保持。

**诊断陷阱**：同一个 `db.get()` 在工作线程里阻塞 30s 只占 1 个线程（请求变慢）；
在**事件循环**上阻塞 30s 则全进程静默（致命）。区分二者要看 `py-spy` 采样时
`MainThread` 停在 `select`（空闲）还是停在 `do_execute`/`get`（被钉住）。

**回归**：`tests/test_provider_proxy.py` 的
`test_per_chunk_gate_check_never_runs_on_the_event_loop` 与
`test_stream_does_not_pin_a_pool_connection_between_chunks`。变异验证：把复核放回
事件循环、或复用请求级 Session，对应用例必须失败。

**已知未修（独立缺陷）**：连接池上限（15）相对总连接需求偏小——steward 的 4 个 core
线程 + 每空间 2 个 assist + 心跳/lease 轮询共同争抢。实测慢响应时 4 个线程在等连接、
仅 2 个在执行。事件循环修复后，这表现为「个别请求变慢」而非全进程静默。

## 16. run token 随心跳续期（09-30）

**触发**：改动 `agent_tokens.issue_run_token`、`AGENT_RUN_TOKEN_TTL_SECONDS*`、
`HeartbeatOut`、sidecar 的 `startHeartbeat`，或任何 run 生命周期超过 token TTL 的路径前必读。

**缺陷（已受控定位）**：run token 只在**租约时签发一次**，而 TTL 上限
`AGENT_RUN_TOKEN_TTL_SECONDS_MAX` 是 **600s**。心跳复用同一 token，因此任何存活超过
600 秒的 run 都会在心跳时收到 **401**；sidecar 把 `[401,403,409,410]` 当作租约失效
（`worker.ts`）并立即 abort。两个独立样本的 401 均出现在 token 到期后 8–18 秒内
（run 232：租约 12:56:09 → 到期 13:06:09 → 401 13:06:17；run 244 同理）。

这是**运行时长的确定性函数**，不是偶发：租约随心跳续期，token 不续，是设计缺口。
存活较短的 run（如 2 分钟）不受影响，因此曾被误认为间歇性故障。

**合同**：

- `HeartbeatOut.run_token` 在每次心跳回传**重新签发**的 token；sidecar 收到后替换
  当前 token（`job.run_token`），其余请求按调用时刻读取，无需额外传播；
- 续签**只复制已校验 token 的 claims**（identity/space/attempt/viewer/allowlist），
  不得借续签扩大 scope 或绕过 fence；per-kind 必含集仍按 kind 传
  （assistant 给 account_id、steward 不给），传错会 fail-closed；
- 字段 additive，旧客户端忽略未知字段，因此后端可先部署；
- **不得**用「调大 TTL」替代续签：那只是把问题推迟到更长的 run。

**回归**：`tests/test_steward_child_run_acceptance.py::test_heartbeat_reissues_the_run_token`
（断言新 token 可用**且 claims 与原 token 一致**，而不只是「返回了字符串」）、
`agent/test/client.test.ts` 的 `returns the reissued run token from heartbeat`。
变异验证：服务端不续签 → 后端用例失败；client 丢弃 token → sidecar 用例失败。

**已知未修（独立缺陷）**：连接池上限（15）在尖峰时不足——steward 的 4 个 core 线程
（`execute()` 在 `write_transaction` 内跑完整 job）+ 每空间 2 个 assist + 心跳/lease
轮询共同争抢。事件循环阻塞已修后，表现为**心跳被拖慢至租约过期**（实测 run 252：
心跳空档 157s → 409 → 失租）。注意 `expired` 的原因可能不同：**401 = token 过期**，
**409 = 服务端已判失租**（如租约被拖过期），两者在「心跳失败」的表象下成因不同，
排查时必须逐条核对响应码。
