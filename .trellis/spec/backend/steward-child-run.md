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
| `POST /internal/agent/steward/attempts/lease` | **独立端点**，`STEWARD_ENABLED` **AND** `STEWARD_PI_RUNTIME_ENABLED` 双开关 503；无可租 204 |
| `POST /internal/agent/jobs/lease` | **保持 `kind="assistant"`**，不放开 |
| `POST /jobs/{id}/heartbeat` | steward 分支**同一立即事务**内同时续 run 与 attempt lease |
| `GET /runs/{id}/context` | `session_id`/`account_id` 为 null、`messages: []`、带 `steward_prompt_version` |
| `POST /runs/{id}/events/append` | `run.kind == 'steward'` 时**拒绝消息类事件**（422） |
| `POST /runs/{id}/settle` | steward 分支经 `settle_run(on_settled=...)` **同事务**结算 attempt |
| `POST /runs/{id}/tools/{tool}/execute` | 复用；allowlist 按 kind |

- **为什么独立端点而不是放开 `LeaseRequest.kind`**：「哪个容器能租哪类作业」必须是**路由级**
  约束，否则任何持 service token 的调用者（含被入侵的 assistant 容器）都能消费 steward 队列。
- **settle 必须同事务**：分两次提交会在崩溃时留下「run succeeded / attempt 仍 in_flight」，
  即 R2 禁止的双终态。实现点是 `agent_queue.settle_run` 的 `on_settled` 钩子。
- 双开关是刻意的：「引擎开启」与「执行载体切换」是两个独立发布决策，必须能各自回退。
- `_authorize_assistant_run` 入口断言 `agent_kind == 'assistant'`，反之亦然。
- `_reject_user_jwt` 必须**先于** kind 探测：否则 user JWT 会从 403 变成 401。

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
- `toolNamesFor(kind)`：steward 在 S1 为**空集**，且必须显式空而非「assistant 减去黑名单」，
  否则新增 assistant 工具会默认成为 steward 工具。

## 8. prompt 版本（跨层字面量）

prompt 文本住在 sidecar 镜像里，服务端不再持有文本，因此 `prompt_version()` 的哈希不再能锚定
评测报告。现行合同：

- `steward_assist.STEWARD_PROMPT_VERSION` 是锚点，经 context 投影下发给 sidecar；
- sidecar `STEWARD_PROMPT_VERSION` 不匹配时 **fail-closed**（不建 session、不发模型请求）；
- 两侧**逐字断言**该字面量（`test_agent_execution_fence.py` 与 `agent/test/worker-slots.test.ts`），
  单侧改名必须失败测试，而不是运行时拒绝所有 steward run。
- prompt cache key 按 kind 派生：assistant 保持 `fg-${account_id}-${session_id}`；
  steward 用 `fg-steward-${space_id}`（assistant 公式对 steward 会得出 `fg-null-null`，
  让不相关空间共享同一上游缓存前缀）。

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
（行为等价 + 受控 E2E + E1 发送门与 fence 调用点断言）、`agent/test/worker-slots.test.ts`（槽位隔离四条）、
`agent/test/poll-scheduling.test.ts`（调度与槽位预算）。

## 12. E1 执行单元不变量（违反会静默停摆）

- **发送门唯一且必须 sweep**：`_fence_check` 只允许三个调用点——`lease_attempt`（发送门）、
  `settle_attempt`（写回门）、`recover_stuck_attempts`（补做被中断的写回）；AST 断言在
  `test_steward_child_run_acceptance.py::test_the_write_back_fence_has_exactly_two_call_sites`。
  发送门遇到被栅栏拦下的 attempt 必须**当场退休为 `skipped` + 安全原因码并继续看下一个候选**，
  不得直接返回：没有别的地方会再租它，留在 `reserved` 会让本空间永远看起来有活干，
  且 `plan_error_code` 永远为空（原因码只有栅栏跑过才存在）。
- **plan 无状态**：`steward_assist_plans` 是不可变快照。「这批工作结果如何」一律由
  `plan_outcome` / `plan_error_code` 从 attempt 派生，派生规则逐条复现旧 `_apply_batch` 的终态码。
- **HTTP 不在写事务内**：发送所需的 runtime/投影/输出上界在租约事务内读出并放进 grant，
  出事务后才发送；载体 `execute` 不读不写数据库。

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
           on_settled=lambda s, r: steward_assist.settle_attempt(
               s, attempt_id=attempt_id, status=body.status, lease_owner=owner))
```

```python
for attempt in candidates:                    # 发送门：退休并继续，而不是放弃
    reason = _fence_check(db, plan, attempt, attempt.assist_kind)
    if reason is not None:
        attempt.status = "skipped"; attempt.error_code = reason
        continue
    break
```
