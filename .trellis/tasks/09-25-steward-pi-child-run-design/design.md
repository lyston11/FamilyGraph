# Steward Pi child run 技术设计

> 依据：`prd.md`（本任务需求与验收）+ `archive/2026-08/08-26-v2-agent-system/`（原始双 Agent 设计）+ `archive/2026-08/08-29-v2-agent-architecture-release-closure/`（Pi Steward Orchestrator 裁定）+ `archive/2026-09/09-01-agent-runtime-assistant-only/`（runtime 收口与「另立任务」要求）。
> 本文件**只做设计**，不含实施。行号锚点基于 2026-09-25 工作区（HEAD `bda8a35`）。

---

## 1. 目标与范围

把 Steward 的模型执行从「进程内裸 httpx 直连 Provider」改回 Pi 运行时，使两条 Agent 链路**共享同一套执行合同**（run 状态机、lease/heartbeat/attempt、run token、context 投影、事件流、provider 网关、egress 审计、错误分类与分层重试、取消门禁），同时**不重建** `agent_jobs` 的第二队列。

本设计**不实施**。实施按 §12 拆为 Stage 1/2/3 三个独立可验收子任务。

---

## 2. 关键裁定总览

| # | 裁定 | 理由摘要 | 代价 |
|---|---|---|---|
| **D1** | **执行记录复用** `agent_runs` + `agent_run_events`，`kind` 扩展为 `'assistant' \| 'steward'` | 这两张表是纯执行记录（状态机/attempt/lease/事件流/幂等 seq/timing），不含账号语义；复制一份会让取消语义、终态归属、审计口径三处长期漂移 | 见 §4 迁移与 §5 身份泛化 |
| **D2** | **scope 载体新增** `steward_runs` 表（1:1 于 child run） | Steward 的 scope 是 `(space, steward_job, batch, viewer?)`，不是 `(account, session)`；用一张窄表承载，避免把 account 语义塞进 `agent_sessions` | 新表 + 一条 FK |
| **D3** | `agent_sessions` **保持 assistant-only**，Steward **不建 session** | `agent_sessions.account_id` 是 `NOT NULL`；Steward 无单一账号，伪造账号行就是数据污染。让「Steward 无 session」成为 DB 强制不变量 | `agent_runs.session_id` 改 nullable |
| **D4** | `agent_jobs` **保持 assistant-only**，Steward child run 的 `job_id` 为 NULL | 09-01 记录的红线：禁止 `AgentJob(kind="steward")` 形成第二套活跃队列。child run 的 lease 来自 `steward_assist_batches` | 无（DB CHECK 不变） |
| **D5** | `provider_proxy` **零改动** | 因为复用 `agent_runs`，网关要读的 `status`/`cancel_requested`/`runtime_snapshot_json` 全部天然存在。egress 审计、上游 4xx 保真、`x-should-retry`、分层重试、流中取消复核**自动生效** | 无 |
| **D6** | Steward 的取消语义**复用** `agent_runs.cancel_requested` 同一列 | 由 Steward 侧在「batch lease 丢失 / 父 job 终态」时置位，与 assistant 的 `request_cancel` 同列同义 → 网关与 fence 无需分支 | Steward 侧新增置位点 |
| **D7** | 身份层**拆成两个函数**，不用 `if` 分流 | `fence_execution` 是安全关键；单函数按 kind 分支时，漏一条 membership 检查就是一个后门。两个函数各有独立回归矩阵 | 代码重复 ~40 行（可接受） |
| **D8** | **现有 sidecar 改多槽并发**（`FG_AGENT_ROLE=both`），不新增容器 | 用户裁定（2026-09-25）：改 sidecar 而非加容器。单容器同时租 assistant 与 steward 两类 job，槽位预算隔离保证 steward 的长调用不挤占 assistant | **动 assistant 热路径**（见 §7）；`worker.ts` 单例假设必须整体重构，回归面最大 |
| **D9** | Steward child run **只发非消息类事件** | `message.assistant_added` 会物化 `AgentMessage` 并调 `agent_citations.project_message_citations(db, message, account)`——Steward 没有 account，也没有面向用户的消息 | 事件校验新增 fail-closed 分支 |
| **D10** | `StewardModelCall` **保留为记账账本**，新增 `run_id` | 分工明确：child run 是**执行**，`StewardModelCall` 是**预算/计费/attempt 状态**。两者同事务结算 → 满足「同一领域服务结算、无双终态」 | 一列 + 索引 |
| **D11** | `admin_agent_latency` **必须加 kind 过滤**，且与 kind 扩展**同批发布** | 否则 steward run 会静默混进 assistant 的 p50/p95，污染既有指标口径（同类教训见 memory #453 的部署顺序硬约束） | 端点加参数 |
| **D12** | 工具化（Stage 3）**独立设计**，本文件只给方向与红线 | Steward 工具需要「space-scoped 而非 account-scoped」的新授权形状，且会把投影边界从「服务端一次给全」变成「逐次工具调用授权」——这是真实的安全面变化 | Stage 3 需单独 design |

---

## 3. 边界与红线（不可削弱）

| 红线 | 现状实现点 | 本设计如何保持 |
|---|---|---|
| sidecar 不持 Provider 凭据 | `session.ts` 强制 `base_url` 以 `/internal/` 开头、`api_key === null` | child run 沿用同一投影；`ContextProviderOut.api_key` 恒 `None` |
| 唯一 egress 在 FastAPI | `provider_proxy` + compose `backend` 网络 `internal:true` | D5：网关零改动；新容器仍只挂 `backend` 网络 |
| 每次出站恰好一条审计 | `_audit_egress`（`error_class`/`retryable`/`sent`/`header_ms`） | 复用；`target_id` = child run id |
| 每次内部请求重算授权 | `_authorize_run` 的 `SpaceMember(status='active')` 查询 | steward 侧同判据（§5.3） |
| 受众限定输入投影 | `steward_guard.project_*_input`（节点代号、无 `User.name`） | 投影生成仍在服务端；sidecar 只收到投影结果 |
| 封闭输出 schema | `steward_guard.validate_*_output` | 写回路径不变（§11） |
| 写回栅栏 + CAS | `_fence_check` + `_apply_batch` | 不变 |
| attempt 预算与保守计费 | `_reserve_attempt` / `_bill_usage` | 不变；`unknown` 计费且不自动重放 |
| 四个崩溃恢复点 | `recover_stuck_batches` | 扩展覆盖「child run 卡在 leased/running」 |
| Steward shared-only | `memory_rag.search_rag` 的 `is_assistant` 谓词；`context_builder` 拒绝 steward 伪造 AgentRun | **注意**：`context_builder.build` 现在**显式拒绝** `agent_kind=="steward" and run_id is not None`（`context_builder.py:171`）。本设计必须把这条**整体拒绝**改为**正向校验**（run_id 必须解析到 `steward_runs` 行），而不是删掉守卫——见 §8.2 |
| 浏览器面不暴露 Steward run | `api/agent.py._own_run_or_404` | 显式加 `agent_kind == 'assistant'`（不依赖 NULL 比较的巧合），见 §5.4 |

---

## 4. 数据模型（迁移 `0055_steward_child_run`）

> 沿用 0024 的模式：**refusal guards 在任何 DDL 或 Alembic 版本移动之前执行**（memory #398）。迁移编号接 `0054_seed_household_roster_fix`。

### 4.1 refusal guards（先于 DDL）

任何一条命中即 `RuntimeError` 中止，报告表/行/值，原库保持可恢复：

1. `agent_runs` 存在 `session_id IS NULL` 的行（新 CHECK 要求 assistant 必须有 session）。
2. `agent_runs` 存在 `kind NOT IN ('assistant','steward')` 或 `kind IS NULL`。
3. `agent_jobs` 存在 `kind != 'assistant'`（队列红线不得被历史数据破坏）。
4. `agent_sessions` 存在 `agent_kind != 'assistant'`（D3 不变量）。
5. `agent_runs` 存在 `job_id IS NOT NULL AND session_id IS NULL` 的行。

### 4.2 `agent_runs`：kind 扩展 + session 可空

```
kind        VARCHAR(16) NOT NULL  CHECK (kind IN ('assistant','steward'))
session_id  INTEGER               NULL      -- 原 NOT NULL
job_id      INTEGER               NULL      -- 语义不变
新增 CHECK  ck_agent_runs_scope_binding:
  (kind = 'assistant' AND session_id IS NOT NULL)
  OR
  (kind = 'steward'   AND session_id IS NULL AND job_id IS NULL)
```

- **`uq_agent_runs_session_active`**（partial unique，`WHERE status IN ('queued','leased','running')`）**保持不变**。`session_id IS NULL` 的行不参与该索引 → Steward 天然不受「每会话一个 active run」约束，这正是我们要的语义（Steward 的并发由 batch lease 表达）。
- 重建表时保留：`uq_agent_runs_job_id`、`fk_agent_runs_job_id_agent_jobs`、`ix_agent_runs_session_id`、`first_leased_at`、`runtime_snapshot_json`、`cancel_requested` 等全部列与约束。
- `agent_sessions` 的 `trg_agent_sessions_scope_immutable` trigger 不受影响（Steward 不建 session）。

### 4.3 `agent_sessions` / `agent_jobs`：**不变**

- `agent_sessions.agent_kind = 'assistant'`（CHECK 保持）。
- `agent_jobs.kind = 'assistant'`（CHECK 保持）+ `uq_agent_jobs_space_active`（历史 steward 索引）**不恢复**。

### 4.4 新表 `steward_runs`

```
steward_runs
  id                 INTEGER PK
  run_id             INTEGER NOT NULL UNIQUE FK agent_runs(id) ON DELETE CASCADE
  steward_job_id     INTEGER NOT NULL      FK steward_jobs(id) ON DELETE CASCADE
  assist_batch_id    INTEGER NULL          FK steward_assist_batches(id) ON DELETE SET NULL
  assist_kind        VARCHAR(16) NOT NULL  CHECK IN ('candidate','ranking','explanation','terminology')
  viewer_account_id  INTEGER NULL          FK accounts(id) ON DELETE CASCADE
  fence_json         JSON NOT NULL DEFAULT '{}'
  created_at         DATETIME NOT NULL

  INDEX ix_steward_runs_job      (steward_job_id)
  INDEX ix_steward_runs_batch    (assist_batch_id)
  INDEX ix_steward_runs_viewer   (viewer_account_id)
  CHECK ck_steward_runs_viewer:
     (assist_kind = 'terminology') = (viewer_account_id IS NOT NULL)
```

- `viewer_account_id` 只在 terminology 有意义（其目标组绑定单个查看者）。candidate/ranking/explanation 是空间级或按收件人分组，**不得**伪造 viewer。
- `fence_json` 与 `StewardAssistBatch.fence_json` 同源（注册时快照），用于 child run 侧的独立复核。
- `ON DELETE CASCADE` 于 `run_id`：child run 被清理时 scope 行随之消失（防御性；正常情况下 Steward run 不由 `prune_finished` 清理，见 §4.6）。

### 4.5 `steward_model_calls`：加 `run_id`

```
run_id  INTEGER NULL FK agent_runs(id) ON DELETE SET NULL
INDEX ix_smc_run (run_id)
```

- `ON DELETE SET NULL` 而非 CASCADE：**记账账本必须比执行记录活得久**。child run 被清理后，prompt digest / token 用量 / 状态机 / error_code 仍在，供预算与审计追溯。
- `run_id` 为 NULL 的历史行语义 = 「in-process 时代的 attempt」，读取方按既有路径处理，**不回填**。

### 4.6 保留与清理

- `agent_queue.prune_finished` 增加 `AgentRun.kind == 'assistant'` 过滤。理由：Steward run 的保留策略应由 steward 侧 GC（`steward_gc`）决定，而不是被 assistant 的 `settled_at` 阈值顺手删掉。
- `agent_queue.reaper_pass` **天然不覆盖** Steward run（它选 `AgentJob`，而 Steward run 无 `AgentJob`）。这既意味着「不会被误改」，也意味着「不能依赖它收敛」→ Steward run 的租约收敛必须由 `steward_assist.recover_stuck_batches` 负责（§11.4）。

### 4.7 downgrade 语义

- 恢复 `agent_runs.session_id NOT NULL`、`kind = 'assistant'`、移除 `ck_agent_runs_scope_binding`。
- **refusal guard 先行**：若已存在 `kind='steward'` 的 run 行，downgrade **必须中止**并报告，绝不静默删除或改写（memory #399：不得破坏性恢复）。给出人工处置指引（先归档/清理 child run 证据，或保留在 upgrade 状态）。
- `steward_runs` 表 drop、`steward_model_calls.run_id` drop。

---

## 5. 执行身份与授权

### 5.1 身份拆为两个类型（D7）

```python
# services/agent_execution.py
@dataclass(frozen=True)
class AssistantExecution:
    run_id: int
    job_id: int
    expected_attempt: int
    account_id: int
    space_id: int
    tool_allowlist: tuple[str, ...]

@dataclass(frozen=True)
class StewardExecution:
    run_id: int
    steward_job_id: int
    assist_batch_id: int | None
    expected_attempt: int
    space_id: int
    viewer_account_id: int | None
    tool_allowlist: tuple[str, ...]

Execution = AssistantExecution | StewardExecution
```

`agent_kind` 不再是字段而是**类型本身**——这是把「kind 分支」从运行时判断变成类型区分的关键。

### 5.2 两个 fence 函数

```python
def fence_assistant_execution(db, identity: AssistantExecution, *, allowed_statuses=("leased","running"),
                              allow_cancel_requested=False) -> tuple[AgentRun, AgentSession, AgentJob]
def fence_steward_execution(db, identity: StewardExecution, *, allowed_statuses=("leased","running"),
                            allow_cancel_requested=False) -> tuple[AgentRun, StewardRun, StewardJob]
```

两者共享同一底层原语（`acquire_run_writer` + `populate_existing` 刷新 + 逐项比对），但**检查集各自完整**：

| 检查项 | assistant | steward |
|---|---|---|
| `run.kind` | `'assistant'` | `'steward'` |
| run↔job 双向 | `run.job_id == job.id` 且 `job.run_id == run.id` | `steward_runs.run_id == run.id` 且 `steward_runs.steward_job_id == job.id` |
| attempt | `run.attempt == job.attempt == identity.expected_attempt` | `run.attempt == identity.expected_attempt`（父 job 的 attempt **不**参与：batch 有独立 attempt，见 §11.2） |
| scope | `session.account_id` / `session.space_id` / `job.account_id` / `job.space_id` | `steward_runs.space_id`（经 job）/ `steward_runs.viewer_account_id`（terminology 时非空且与 identity 一致） |
| allowlist | `run.tool_allowlist_json` 排序比对 | 同 |
| **membership** | 账号对应 user 在该空间 `status='active'` | 分层判据（§5.2.1）：父 job 活跃 + 空间存在 + `viewer_account_id` 非空时该账号仍 active |
| 状态 | `run.status`/`job.status` in allowed | `run.status` in allowed |
| lease | run/job 双 lease 未过期 | run lease 未过期 **且** batch lease 未过期（§11.2） |
| cancel | `run.cancel_requested or job.cancel_requested` | `run.cancel_requested` |

#### 5.2.1 membership 判据（已裁定：接受分层判据，2026-09-25）

Steward child run 是**空间级**执行，不携带单一账号，因此 assistant 的「账号 membership」判据不适用。**用户已裁定接受分层判据**，三层依次校验：

1. **父 `StewardJob` 仍活跃**（`status IN ('leased','running')`）——Steward 的授权根。父 job 存活本身要求该空间启用 Steward 且有活跃空间成员（`steward_demand.register` 的 `authorize()`）。
2. **空间存在且未被删除**。
3. **`viewer_account_id` 非空时（terminology），该账号对应用户必须是该空间的 active member**——与 assistant 同判据。

**已接受的残留风险（必须如实登记，不得淡化）**：

- 第 2 层是**弱锚**：它只证明「空间还活着」，**不阻止一个已被撤权的用户的数据被继续处理**。
- 与 assistant 的差距是**收敛延迟**：assistant 是「撤权 → 下一次内部请求即 403」；Steward 依赖 ①父 job 终态化置位 `cancel_requested`，②`recover_stuck_batches` 在**一个维护 tick 内**收敛。即存在一个有界的延迟窗口。
- 影响面有界：空间级 assist 处理的是**空间已确认事实**，不是某个人的私有数据；`viewer_account_id` 非空的 terminology 路径仍受第 3 层保护。

**必须有的回归**（否则该风险无守护）：

- 空间成员被撤权后，在途 child run 在**一个维护 tick 内**终态化（对应 09-19「撤权不收敛」教训）。
- 父 job 终态化后，在途 child run 不得继续出站（网关 `_require_executable_run` + `cancel_requested` 双门禁）。
- 移除第 1 层或第 3 层任一检查，上述回归必须失败（证明它在守护东西）。

### 5.3 `_authorize_run` 分流

`api/internal_agent.py._authorize_run` 按 token `typ` + `agent_kind` 分流为 `_authorize_assistant_run` / `_authorize_steward_run`。共享部分（`_reject_user_jwt` / token 解码 / 审计拒绝）不动。

### 5.4 浏览器面与 admin 面隔离

- `api/agent.py`：`_own_session_or_404` 与 `_own_run_or_404` 增加 `agent_kind == 'assistant'` 条件。**显式条件**，不依赖 `None != account_id` 的巧合求值。
- `api/agent.py` 的 `/sessions` 列表已按 `AgentSession.account_id` 过滤，Steward 无 session → 天然不出现；仍加显式断言。
- admin 面：`admin_read_model.agent_runs` 返回 `account_id=agent_session.account_id` → 对 steward run 为 `None`。`AdminAgentRunOut.account_id` 必须改 `int | None`；`kind` 字段已存在。**admin 读模型不得暴露 Steward 的 `viewer_account_id` 或 prompt 内容**。
- `admin_agent_latency`：见 §10.2。

---

## 6. 内部协议扩展

### 6.1 run token claims（kind 相关）

| claim | assistant | steward |
|---|---|---|
| `typ` | `"agent_run"` | `"agent_run"` |
| `run_id` | ✓ | ✓ |
| `job_id` | `AgentJob.id` | **`StewardJob.id`**（父作业 id，语义重定义） |
| `steward_batch_id` | — | ✓（可空） |
| `attempt` | run attempt | run attempt |
| `agent_kind` | `"assistant"` | `"steward"` |
| `account_id` | ✓ | — |
| `space_id` | ✓ | ✓ |
| `viewer_account_id` | — | ✓（可空，terminology 非空） |
| `tool_allowlist` | ✓ | ✓ |

- `_RUN_REQUIRED_CLAIMS` 改为 **per-kind 表**：`{ "assistant": (...), "steward": (...) }`。`decode_run_token` 先读 `agent_kind`，再按表校验必含 claims。
- 新增回归：逐 kind 断言 claims 全集（防止一侧漂移，沿用「共享字面量在一侧定义常量、另一侧逐字断言」的既有教训）。
- TTL 上限 600s 不变。`jti` 不变。

### 6.2 新增端点：Steward lease

```
POST /internal/agent/steward/jobs/lease
  auth : service token
  body : {kind: "steward", leased_by: str, lease_ttl_seconds?: int}
  resp : 200 flat {job_id(=StewardJob.id), run_id, steward_batch_id?, assist_kind,
                   agent_kind, attempt, tool_allowlist, policy_version, run_token}
         204 empty when nothing due
```

- **为什么单独端点而不是放开 `/jobs/lease` 的 `Literal["assistant"]`**：现有端点被 09-01 明确定义为「Assistant sidecar 专用」，放开它会让任何 service-token 调用者（含被入侵的 assistant 容器）消费 Steward 队列。独立端点让「哪个容器能租哪类作业」成为**路由级**约束，而不是 payload 校验。
- 端点内 `STEWARD_ENABLED` 与新增 `STEWARD_PI_RUNTIME_ENABLED` 双开关门禁（关闭时 503）。
- 服务层 `steward_assist.lease_child_run(db, leased_by, ttl)`：在 `_immediate_tx` 内选取 `status='pending'` 且 `next_attempt_at <= now` 的 `StewardAssistBatch`（复用现有 `schedule_due_batch` 的选择与 `_fence_check` 逻辑），预留 attempt 行、创建 `agent_runs` + `steward_runs` 行，返回 grant。**无网络调用**。

### 6.3 既有端点的 steward 兼容

| 端点 | steward 变化 |
|---|---|
| `POST /runs/{id}/heartbeat` | 复用；续租 run 的同时续租 batch lease（同一立即事务，§11.2） |
| `GET /runs/{id}/context` | `ContextOut.account_id` → `int \| None`；`session_id` → `int \| None`；`messages` 对 steward 为 `[]`；`context_blocks` 携带 steward 投影 |
| `POST /runs/{id}/provider/{chat/completions\|responses}` | **零改动**（D5） |
| `POST /runs/{id}/events/append` | 新增 fail-closed：`run.kind == 'steward'` 时禁止消息类事件（D9） |
| `POST /runs/{id}/tools/{tool}/execute` | 复用；`check_scope` 改为读 `StewardRun`/`AssistantExecution` 的 allowlist 与 kind |
| `POST /runs/{id}/settle` | steward 分支在落终态后调用 `steward_assist.settle_child_run(...)`（§11.3） |

### 6.4 `ContextOut` schema 变更（两侧同批）

```python
class ContextOut(BaseModel):
    run_id: int
    session_id: int | None          # was int
    agent_kind: AgentKind           # 扩展为 union
    account_id: int | None          # was int
    space_id: int
    ...
```

sidecar `client.ts` 的严格解码（现要求 `run_id/session_id/account_id/space_id` 均为正整数）必须按 `agent_kind` 分支：assistant 保持原判据；steward 要求 `session_id === null && account_id === null`，并对 `steward_batch_id`/`viewer_account_id` 做同样的严格校验。**这是「跨层安全边界」**（spec §9 的 ContextOut 解码门禁），不得用默认值「修复」畸形投影。

---

## 7. Sidecar 多槽并发（D8，已裁定）

> 用户裁定（2026-09-25）：**改现有 sidecar**，不新增容器。本节取代原「`agent-steward` 容器」方案。

### 7.1 单容器双 kind

同一进程同时租两类 job，由 `FG_AGENT_ROLE` 控制：

```
FG_AGENT_ROLE=assistant | steward | both    # 新增；缺失默认 assistant（向后兼容）
```

| 维度 | assistant job | steward job |
|---|---|---|
| lease 端点 | `POST /internal/agent/jobs/lease`（`kind="assistant"`） | `POST /internal/agent/steward/jobs/lease`（`kind="steward"`） |
| system prompt | `ASSISTANT_SYSTEM_PROMPT` | **新增** `STEWARD_SYSTEM_PROMPT`（§8.1） |
| tool allowlist | 服务端下发，assistant 注册项 | 服务端下发，steward 注册项 |
| **槽位预算** | `AGENT_MAX_CONCURRENT_RUNS`（默认 2，与 `AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT` 对齐） | `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（默认 1） |
| prompt cache key | `fg-${account_id}-${session_id}` | `fg-steward-${space_id}-${steward_job_id}` |

- `both` 模式下**槽位预算独立**：steward 的长调用（30s 级）不得挤占 assistant 槽位。这是选 A2 而不选 A1 的核心原因。
- 单 kind 模式（`assistant` 或 `steward`）仍受支持，用于回退与隔离排查。
- **一个 job 只租一种 kind**；projection 的 `agent_kind` 与 job 不符时立即抛错（沿用现有 fail-closed 风格）。

### 7.2 `worker.ts` 的重构面（**这是本设计的最大风险项**）

现状是**严格单槽**，单例假设散落在以下位置，必须**全部**改造：

| 位置 | 现状 | 改为 |
|---|---|---|
| `private active: ActiveRun \| null` | 单例 | `private active: Map<string, ActiveRun>`（key = `run_id`） |
| `tryLeaseAndRun()` | `if (this.active !== null) return false` | `tryLeaseAndRun(kind)`：按 kind 查槽位占用；满则不租 |
| `pollLoop()` | `if (this.active === null)` 后 `await` | 对每个已启用 kind 尝试补满槽位（详见 §7.3） |
| `markLeaseLost(runId)` | `this.active?.job.run_id === runId` | `this.active.get(runId)`（**改造后更简单**：已是按 run_id 定位） |
| `markCancelRequested(runId)` | 同上 | 同上 |
| `get isBusy()` | `this.active !== null` | `this.active.size > 0`（当前**无调用方**，仅 API 兼容） |

**已经天然隔离、无需改动的部分**（这是 A2 可行性的依据）：

- 每 run 状态已封在 `ActiveRun` 里：`heartbeatTimer` / `abort` / `leaseLost` / `cancelRequested`。**并发安全**。
- `RunEventBuffer` 在 `executeJob` 内**按 run 新建**（`new RunEventBuffer(projection.next_event_seq, ...)`），不跨 run 共享。
- `startEventFlusher()` 的 `pending` / `pumpPromise` 都是**函数局部**变量，每个 run 一份。
- 模块级可变状态只有 `tools.ts` 的 `CANONICAL_BY_PROVIDER_WIRE_NAME`（模块初始化时填充，只读）。**无共享可变执行态**。
- `health.ts` 不读 `isBusy`；健康契约（`/healthz` 200、`/readyz` 503）与并发数无关，不变。
- **`InternalClient` 是并发安全的**：它只有 `fetchImpl`/`backoff`/`nowMs` 三个只读字段，加上 `onRunCancelled` 回调。

### 7.2.1 跨槽回调隔离（**必须显式处理，不是自然成立的**）

`InternalClient.notifyRunCancelled(path)` 从**请求路径**解析 run_id 再调用 `onRunCancelled(runId)`，因此回调本身已带 run_id，不会串槽。但 `worker.ts` 在构造函数里**只注册一次**：

```ts
this.client.onRunCancelled = (runId) => this.markCancelRequested(runId);
```

并发后仍正确（`markCancelRequested` 按 run_id 查表），**前提是 `markCancelRequested` / `markLeaseLost` 改造成按 id 定位**（§7.2 已列）。若保留 `this.active?.job.run_id === runId` 的写法，第一个槽取消后会**静默忽略**其他槽的取消 —— 这是改造中最容易漏的一处。

### 7.3 并发模型与槽位预留（**修正：原表述过强**）

> ⚠️ **纠正一处早期结论**。设计初稿声称「两个并发租约调用都会看到空槽，于是租到两个 job 但只有一个槽」，并称之为「JS 单线程 + await 的必然结果」。**该断言过强，已核实不准确**，两个理由：
> 1. `pollLoop` 是 `await this.tryLeaseAndRun()` —— **顺序等待**，不是 fire-and-forget。今天不存在并发 `leaseJob()` 调用。
> 2. `agent_queue.lease_next` 在**空队列时也执行 `BEGIN IMMEDIATE`**（`with _immediate_tx(db)` 包住整个查询，`if job is None: return None` 在锁内），因此空轮询也是**串行化**的，额外消除了大部分竞争窗口。
>
> 结论：预留**仍然需要**，但理由是**面向未来的正确性**而非「必然发生的 bug」：多槽实现几乎必然会同时发起多个租约（否则拿不回并发收益），那一刻预留就从「防御」变成「必需」。按「先预留再 await」写，两种实现都正确。
>
> **补一个不因串行化而消失的风险**：SQLite 写锁只能保证两个并发租约**不会拿到同一个 job**；它们会依次拿到**两个不同的** queued job。因此「租到 2 个 job、只有 1 个槽」这个后果仍然真实存在——这正是预留要解决的问题，与是否串行无关。

**实现约束（任一多槽写法都必须满足）**：

1. **槽位预留**：在 `Map` 中先插入占位条目，再 `await leaseJob()`；204 → 删占位；成功 → 用真实 `ActiveRun` 替换；异常 → 删占位。
2. **`leaseJob()` 的 await 窗口内不得让槽位「看起来空闲」**：占位条目必须让 `countInFlight(kind)` 把它算进去。
3. **失败不得泄漏槽位**：`try/finally` 保证占位在异常路径也被清理（否则槽位永久泄漏，并发度单调下降到 0）。
4. **不得 fire-and-forget 到无归属的 promise**：`executeJob` 自身在 `try/catch` 内收敛（catch 里会 settle failed + `redactErrorText`），因此后台执行是安全的；但**必须**把 promise 存进 `ActiveRun`（或在 finally 里 `void p.catch(...)`），否则未捕获 rejection 会终止进程。

**参考实现形状**（不作为代码提交，只锁定语义）：

```ts
private async tryLeaseAndRun(kind: AgentKind): Promise<boolean> {
  if (this.inFlight(kind) >= this.slotsFor(kind)) return false;
  const placeholder = `pending:${kind}:${++this.pendingSeq}`;
  this.active.set(placeholder, { pending: true, kind } as ActiveRun);
  try {
    const job = await this.client.leaseJob(kind);
    this.active.delete(placeholder);
    if (job === null) return false;
    const active = { job, abort: new AbortController(), leaseLost: false,
                     cancelRequested: false, heartbeatTimer: ... };
    this.active.set(job.run_id, active);
    const p = this.executeJob(job, active);
    active.done = p;
    void p.finally(() => { clearInterval(active.heartbeatTimer); this.active.delete(job.run_id); });
    return true;
  } catch (error) {
    this.active.delete(placeholder);
    throw error;
  }
}
```

### 7.3.1 槽位数的确定（与 Q1 裁定配套）

| kind | 槽位数 | 来源 | 理由 |
|---|---|---|---|
| assistant | `AGENT_MAX_CONCURRENT_RUNS`（默认 **2**） | 新增 env | 与 `AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT=2` 对齐：后端最多放行 2 个并发 assistant run/账号，sidecar 少于 2 会人为造排队 |
| steward | `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（默认 **1**） | 复用后端既有值 | 与 `schedule_due_batch` 的全局并发门（`in_flight >= MAX_CONCURRENT_BATCHES`）同源，两侧不得漂移 |

- **`both` 模式下两类槽位独立计数**：`inFlight(kind)` 按 `ActiveRun.kind` 过滤。这是「steward 长调用不挤占 assistant」的**唯一实现点**，必须有回归。
- **`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES` 是两侧共享字面量**：后端 `schedule_due_batch` 用它决定是否**再放行**一个 batch，sidecar 用它决定**能跑几个**。若 sidecar 值小于后端值，batch 会「已预留 attempt 但无人执行」，直到 `recover_stuck_batches` 收口。**建议**：sidecar 侧不独立配置，改为从 context/lease 响应读取后端当前值；若不改协议，则两侧必须同名同默认并加逐字断言（沿用 memory #244 的 typ 漂移教训）。

### 7.4 与既有测试的关系

**现有 `poll-scheduling.test.ts` 的三个用例会失效**（已逐条核实）：

| 用例 | 现状断言 | 多槽后为何失效 |
|---|---|---|
| `re-polls immediately after a completed run` | `harness([true,true,true,false])` 后 `calls` **恰好 4** 且间隔 <50ms | 多槽下 `pollLoop` 会在**同一次**迭代内补满多个槽 → 单次 `runUntil(calls, 4)` 可能已触发远超 4 次租约 |
| `waits the configured interval when the queue is empty` | `harness([false,false,false])`，间隔 ≥40ms | 两个 kind 各自空轮询 → 计数翻倍 |
| `defaults the poll interval low enough` | `expect(config.leasePollIntervalMs).toBe(250)` | **不失效**（纯 config 断言） |

另外 `harness()` 用 `vi.spyOn(worker, "tryLeaseAndRun")` 替换实现——改造后该方法**带 kind 参数**，spy 必须改为接收并记录 kind，否则测不到「两类槽位分别补满」。

**适配原则**：不靠「总次数」断言，改为**按 kind 分别断言**（每个 kind 的调用序列），并把「立即重租」的判据改为「同一 kind 仍有空槽时不 sleep」。

`worker.integration.test.ts` 约 20 处 `expect(await worker.tryLeaseAndRun()).toBe(true)`：签名保留（kind 改为可选、默认 assistant），因此**这些断言无需修改**；但其中「单次调用后断言 state 只有 1 个 run」类断言需确认在 `AGENT_MAX_CONCURRENT_RUNS=1` 的测试配置下仍成立（`makeAgentConfig` 需补新字段）。

**新增回归**（A2 引入的新风险，`design.md` §7.2.1 / §7.3.1 各对应一条）：

1. **两槽隔离**：并发两槽各跑一个 run，断言 heartbeat / 事件 append / settle 按 `run_id` 互不串台。
2. **槽位预算隔离**：steward 槽被长调用占满时，assistant 仍能在自己的空槽上租到并完成（**这是 A2 相对 A1 的唯一价值证明，必须有**）。
3. **单槽故障不扩散**：任一槽的 lease 丢失 / 取消，不影响另一槽（同时锁住 §7.2.1 的跨槽回调隔离）。
4. **槽位不泄漏**：`leaseJob()` 抛异常后，槽位数回到初始值（连续 N 次异常后仍能租到）。

### 7.5 compose

`agent` 服务只需新增 env，**不新增容器**：

```yaml
agent:
  environment:
    FG_AGENT_ROLE: ${FG_AGENT_ROLE:-both}
    AGENT_MAX_CONCURRENT_RUNS: ${AGENT_MAX_CONCURRENT_RUNS:-2}
    # 必须与 api 服务的同名变量同值（§7.3.1）：两侧不一致会导致 batch 被预留但无人执行
    STEWARD_ASSIST_MAX_CONCURRENT_BATCHES: ${STEWARD_ASSIST_MAX_CONCURRENT_BATCHES:-1}
    STEWARD_PI_RUNTIME_ENABLED: ${STEWARD_PI_RUNTIME_ENABLED:-0}
    # ...既有变量不变
```

- 网络、卷、端口均不变（仍无外网 egress）。
- `STEWARD_PI_RUNTIME_ENABLED=0` 时即使 `FG_AGENT_ROLE=both` 也不租 steward job（服务端端点本身就 503）。
- `AGENT_MAX_CONCURRENT_RUNS` 只影响 sidecar 本地并发；**不改变后端授权**（`AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT` 仍是真正的上限）。把它设为大于后端值只是多几个空轮询，无安全影响。

### 7.6 被否决的备选（留档）

| 方案 | 成本 | 结论 |
|---|---|---|
| **A1** 单槽串行复用两种 kind | 30s 级 steward 调用阻塞用户提问，打破 assistant 首段 ≤3s 目标 | 否决 |
| **A2** 现有 sidecar 改多槽（**采用**） | 动 assistant 热路径（§7.2）；每-run 状态已天然隔离，改造面集中在 `Map` 替换 + 槽位预留 + 跨槽回调 | **采用** |
| **A3** 新增 `agent-steward` 容器 | 零改动 assistant 热路径，但多一个容器与 healthcheck | 用户未选；若 A2 的回归面在实施中失控，可回退到此方案（两者不冲突） |

### 7.7 隔离性自查结论（A2 可行性的证据基础）

已逐项核实「并发后会不会串台」：

| 共享点 | 是否跨 run 共享 | 结论 |
|---|---|---|
| `this.active` | 是（单例） | **必须改**（§7.2） |
| `ActiveRun.{heartbeatTimer,abort,leaseLost,cancelRequested}` | 否（每 run 一份） | 安全 |
| `RunEventBuffer` | 否（`executeJob` 内新建） | 安全 |
| `startEventFlusher` 的 `pending`/`pumpPromise` | 否（函数局部） | 安全 |
| `InternalClient` 字段 | 是但只读 | 安全 |
| `onRunCancelled` 回调 | 是（单例回调），但**参数带 run_id** | 安全，**前提是 `markCancelRequested` 按 id 定位**（§7.2.1） |
| `tools.ts` `CANONICAL_BY_PROVIDER_WIRE_NAME` | 是但只读 | 安全 |
| `health.ts` | 不读并发态 | 安全 |
| `logger.child({run_id})` | 否（`executeJob` 内） | 安全 |

**结论**：A2 的改造面**确实集中**，与设计初稿的判断一致；但「集中」不等于「小」——`poll-scheduling.test.ts` 需要重写、`worker.integration.test.ts` 的测试配置需补字段，且新增 4 条回归。这是选 A2 必须付的代价。

### 7.8 参考实现（可直接落地的代码形状）

> 本节是 S1 的实施基准。它不是「示意」：类型、槽位计数、异常路径、`executeJob` 的 promise 归属都已定下来。实施时照此写，差异必须在 PR 里说明理由。

#### 7.8.1 `config.ts`

```ts
export type AgentRole = "assistant" | "steward" | "both";
export type AgentKind = "assistant" | "steward";

// AgentConfig 新增：
role: AgentRole;                 // FG_AGENT_ROLE，缺失默认 "assistant"（向后兼容）
maxConcurrentRuns: number;       // AGENT_MAX_CONCURRENT_RUNS，默认 2
stewardMaxConcurrentBatches: number;  // STEWARD_ASSIST_MAX_CONCURRENT_BATCHES，默认 1
```

- `role` 用 `readEnum` 严格解析；未知值 **fail fast**（`process.exit(1)`，与现有配置错误同形），不得静默降级为 `assistant`。
- 两个并发值复用现有 `readInt`，并加上界校验（如 `1..8`，与后端 `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES` 的 `1..8` 校验区间一致）。

#### 7.8.2 `client.ts`

```ts
/** 按 kind 选端点；kind 同时进入请求体（两侧字面量逐字一致）。 */
async leaseJob(kind: AgentKind = "assistant"): Promise<LeasedJob | null> {
  const token = signServiceToken(this.config.serviceSecret, {
    sidecarId: this.config.sidecarId,
    nowMs: this.nowMs(),
  });
  const path =
    kind === "assistant"
      ? "/internal/agent/jobs/lease"
      : "/internal/agent/steward/jobs/lease";
  const { status, json } = await this.request("POST", path, token, {
    kind,
    leased_by: this.config.sidecarId,
  });
  if (status === 204) return null;
  // ...existing normalization...
}
```

`getRunContext()` 的严格解码必须按 `agent_kind` 分支（§6.4）：assistant 要求 `session_id`/`account_id` 为正整数（现状不变）；steward 要求二者为 `null`，并对 `steward_batch_id` / `viewer_account_id` 做同样的严格校验。**不得**用默认值「修复」畸形投影。

#### 7.8.3 `worker.ts` 槽位模型（完整替换现有单例）

```ts
interface PendingSlot {
  pending: true;
  kind: AgentKind;
}

interface ActiveRun {
  pending: false;
  kind: AgentKind;
  job: LeasedJob;
  heartbeatTimer: NodeJS.Timeout;
  abort: AbortController;
  leaseLost: boolean;
  cancelRequested: boolean;
  /** Settles (never rejects) when this slot is released. */
  done?: Promise<void>;
}

type Slot = PendingSlot | ActiveRun;
```

```ts
  /** Keyed by run_id for real runs; pending slots use a synthetic unique key. */
  private readonly slots = new Map<string, Slot>();
  private slotSeq = 0;

  get isBusy(): boolean {
    return this.slots.size > 0;
  }

  /** Test seam: how many slots of this kind are occupied (pending + running). */
  inFlight(kind: AgentKind): number {
    let count = 0;
    for (const slot of this.slots.values()) if (slot.kind === kind) count += 1;
    return count;
  }

  private slotsFor(kind: AgentKind): number {
    return kind === "assistant"
      ? this.config.maxConcurrentRuns
      : this.config.stewardMaxConcurrentBatches;
  }

  private enabledKinds(): AgentKind[] {
    switch (this.config.role) {
      case "assistant":
        return ["assistant"];
      case "steward":
        return ["steward"];
      default:
        return ["assistant", "steward"];
    }
  }

  private async pollLoop(): Promise<void> {
    while (!this.stopped) {
      let didWork = false;
      for (const kind of this.enabledKinds()) {
        // Fill every free slot of this kind. The inner await is sequential on
        // purpose: with the reservation below it is already safe to fan out,
        // but serial leases keep an empty queue at one round trip per poll
        // rather than one per slot.
        while (!this.stopped && this.inFlight(kind) < this.slotsFor(kind)) {
          let leased = false;
          try {
            leased = await this.tryLeaseAndRun(kind);
          } catch (error) {
            // Never let the poll loop die, and never spin on a failing
            // endpoint: a transient lease error just delays the next poll.
            this.logger.warn("poll loop iteration failed", {
              kind,
              error: error instanceof Error ? error.message : String(error),
            });
            break;
          }
          if (!leased) break;
          didWork = true;
        }
      }
      if (didWork) continue;
      await this.sleep(this.config.leasePollIntervalMs);
    }
  }

  /** One lease attempt + slot ownership. Exposed for tests. */
  async tryLeaseAndRun(kind: AgentKind = "assistant"): Promise<boolean> {
    if (!this.enabledKinds().includes(kind)) return false;
    if (this.inFlight(kind) >= this.slotsFor(kind)) return false;

    // Reserve BEFORE awaiting. `leaseJob` is a suspension point, so a
    // concurrent caller would otherwise observe this slot as free and lease a
    // second job that no slot can run — the job would hold a live lease with
    // no executor until the server-side recovery path reclaimed it.
    const reservationKey = `pending:${kind}:${++this.slotSeq}`;
    this.slots.set(reservationKey, { pending: true, kind });

    let job: LeasedJob | null;
    try {
      job = await this.client.leaseJob(kind);
    } catch (error) {
      this.slots.delete(reservationKey); // Never leak a slot on failure.
      throw error;
    }
    if (job === null) {
      this.slots.delete(reservationKey);
      return false;
    }

    const abort = new AbortController();
    const run: ActiveRun = {
      pending: false,
      kind,
      job,
      abort,
      leaseLost: false,
      cancelRequested: false,
      heartbeatTimer: this.startHeartbeat(job, abort.signal),
    };
    this.slots.delete(reservationKey);
    this.slots.set(job.run_id, run);

    // executeJob handles its own failures and never rejects; the catch is still
    // required — an unhandled rejection here would terminate the process.
    run.done = this.executeJob(job, run)
      .catch((error) => {
        this.logger.error("run lifecycle escaped its own error handling", {
          run_id: job.run_id,
          error: error instanceof Error ? error.message : String(error),
        });
      })
      .finally(() => {
        clearInterval(run.heartbeatTimer);
        // Remove only our own entry: a later attempt must not have its slot
        // deleted by a finishing earlier one.
        if (this.slots.get(job.run_id) === run) this.slots.delete(job.run_id);
      });
    return true;
  }

  private markLeaseLost(runId: string): void {
    const run = this.slots.get(runId);
    if (run === undefined || run.pending || run.leaseLost) return;
    run.leaseLost = true;
    run.abort.abort();
    this.logger.warn("lease lost, aborting run", { run_id: runId });
  }

  private markCancelRequested(runId: string): void {
    const run = this.slots.get(runId);
    if (run === undefined || run.pending || run.cancelRequested) return;
    run.cancelRequested = true;
    run.abort.abort();
    // Cancellation is adjudicated server-side; the sidecar stops issuing tool
    // calls and skips settling (never settles "cancelled" itself).
    this.logger.warn("cancel requested by server, stopping tool calls", { run_id: runId });
  }
```

**`executeJob` 内唯一必改的一行**：

```ts
// 现状（硬编码 assistant）
if (projection.agent_kind !== "assistant" || job.agent_kind !== "assistant") {
  throw new Error("sidecar received a non-assistant job");
}
// 改为（按本槽的 kind 校验，一个 job 只租一种 kind）
if (projection.agent_kind !== active.kind || job.agent_kind !== active.kind) {
  throw new Error(`sidecar received a ${job.agent_kind} job on a ${active.kind} slot`);
}
```

其余 `executeJob` 内部对 `active.cancelRequested` / `active.leaseLost` 的读取**全部不变**——这正是 §7.7 自查表里「每-run 状态已隔离」的具体含义。

#### 7.8.4 消除 `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES` 的两侧漂移

§7.3.1 指出的问题是「后端放行 N 个 batch，sidecar 只能跑 M<N 个 → N−M 个 batch 挂着租约无人执行」。**解决方案：让服务端在 lease 响应里广播它当前放行的并发上限**，sidecar 取 `max(本地配置, 服务端广播)`。

协议增量（`schemas/agent.py`，两侧同批）：

```python
class StewardLeaseOut(BaseModel):
    job_id: int          # StewardJob.id
    run_id: int
    steward_batch_id: int | None
    assist_kind: str
    agent_kind: Literal["steward"]
    attempt: int
    tool_allowlist: list[str]
    policy_version: str
    max_concurrent: int  # 服务端当前放行的 batch 并发上限
    run_token: str
```

- sidecar 在首次成功 lease 后用该值上调自己的槽位（只上调、不下调，避免抖动），并在**不一致时记一条 warn 日志**（含两个数值，不含任何业务数据）。
- 这样即使两侧 env 配错，也是「sidecar 多几个空槽」而不是「batch 无人执行」。
- **仍要保留两侧同名同默认**（`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`）作为初始值，并加逐字断言测试——广播是**纠错机制**，不是**替代配置**。
- 若评审认为不必新增协议字段，则退化为「两侧同名同默认 + 逐字断言 + 部署检查清单」，但必须接受「配错即延迟自愈」的代价。

#### 7.8.5 测试脚手架改动

```ts
// poll-scheduling.test.ts —— 重写后的 harness 形状
function harness(leasesByKind: Record<AgentKind, boolean[]>) {
  const config: AgentConfig = {
    ...makeAgentConfig(0),
    leasePollIntervalMs: 50,
    role: "both",
    maxConcurrentRuns: 2,
    stewardMaxConcurrentBatches: 1,
  };
  const worker = new SidecarWorker({ client: {} as never, config, logger: createLogger() });
  const calls: Array<{ kind: AgentKind; at: number }> = [];
  vi.spyOn(worker, "tryLeaseAndRun").mockImplementation(async (kind: AgentKind = "assistant") => {
    calls.push({ kind, at: Date.now() - started });
    const queue = leasesByKind[kind];
    return queue.shift() ?? false;
  });
  return { worker, calls };
}
```

- **判据改变**：不再断言「总调用次数 == N」，改为断言**每个 kind 各自的调用序列**，并把「立即重租」定义为「同一 kind 仍有空槽时不 sleep」。
- `makeAgentConfig` 补 `role` / `maxConcurrentRuns` / `stewardMaxConcurrentBatches` 三个字段（现有约 20 处 `tryLeaseAndRun()` 调用点**无需修改**，因为 kind 参数默认 `assistant`）。
- 新增用例（对应 §7.4 四条）建议放 `agent/test/worker-slots.test.ts`，避免继续撑大 51.5K 的 `worker.integration.test.ts`。

---

## 8. 上下文与 prompt

### 8.1 Steward system prompt 的归属

- 新增 `agent/src/prompts/steward.ts` 导出 `STEWARD_SYSTEM_PROMPT`。
- **内容约束**（Stage 1 只需骨架，Stage 2 补齐）：
  - 明确「你的输出是结构化候选，不是面向用户的回答」；
  - 明确「只可使用给定节点代号与证据 id，不得引入任何其他人物或事实」；
  - 明确「不得编造关系、不得输出自由文本（除声明字段）」；
  - 明确「不得请求未授权数据；工具返回空即视为资料不足」。
- **版本化**：prompt 文本变更必须同步 `steward_assist.prompt_version()`（现由文本哈希派生，见 `steward_assist.py:561`）。prompt 迁移到 sidecar 后，该函数必须改为**由服务端持有 prompt 版本常量**（因为文本不再在服务端）。**这是必须处理的耦合点**：否则 `prompt_version()` 会失去意义，评测报告的版本锚点失效。
  - 方案：服务端保留 `STEWARD_PROMPT_VERSION` 常量，sidecar 启动时通过一个内部端点（或 context 投影字段）**上报**它实际加载的 prompt 版本；不匹配时 fail-closed 拒绝执行。这同时防止「sidecar 镜像过期、prompt 与后端不匹配」的静默漂移。
  - 这是**跨层字面量**，按既有教训（memory #244 的 typ 漂移）必须两侧各定义常量 + 逐字断言。

### 8.2 context 投影

- `context_builder.build(agent_kind="steward", run_id=<steward run id>)` 的守卫改造：
  - **现状**（`context_builder.py:171`）：`agent_kind == "steward" and run_id is not None` → 422「Steward consumer 不得伪造 generic AgentRun」。
  - **改为**：`agent_kind == "steward"` 时，`run_id` 必须解析到 `steward_runs` 行，且该行 `steward_job_id` 与投影的 space 一致；否则 422。守卫**收紧为正向校验**，而不是删除。
- 投影内容（`context_blocks`）由服务端从 `steward_guard` 的白名单字段构造，**不**复用 assistant 的 RAG 检索路径作为默认来源：
  - Steward 的输入是「已确认事实摘要 + 节点代号花名册」，不是 FTS 检索结果；
  - 若未来要接 shared RAG（`09-11-steward-capability-followups` R1），走 `search_rag(agent_kind="steward")` 的 shared-only 谓词，**必须**保留 `is_assistant=0` 分支（private/public 分支对它永久关闭）。
- `ContextBuild.run_id` FK 天然可用（steward run 也是 `agent_runs` 行）→ 无需改 `context_builds` 表。
- `ContextBuild.agent_kind` 列宽 16，`'steward'` 可容纳。

### 8.3 会话历史

- Steward child run **无会话历史**（`messages: []`）。每次 assist 是单轮、无跨 run 上下文。
- 理由：assist 的输入是**服务端投影**而非对话；引入历史会让「同一语义哈希 → 同一输出」的幂等与去重（`request_hash_for`、`last_checked_hash`）失效。
- 开放问题 Q4 允许用户改判；若要求历史，则必须同时重设计去重键。

---

## 9. 工具面（Stage 3）

### 9.1 为什么工具化是真正的新增风险

现有 `steward_guard` 的模型是「服务端一次给全、模型只回封闭 JSON」。加工具后，模型可以**主动请求更多数据**，投影边界从「一次性」变成「逐次调用」。因此：

- 每个 steward 工具必须是**只读**、**space-scoped（或 viewer-scoped）**、**代号化输出**。
- 授权必须在服务端**每次**执行时重算（不得缓存上一次的授权结论）。
- 工具结果必须经过与 assistant 相同的 `policy_guard.tool_result_hook` 与输出上限。

### 9.2 授权形状（需独立设计）

| 工具类型 | scope | 示例（**仅示意，不在本设计定稿**） |
|---|---|---|
| 空间级只读 | `space_id`（无 account） | 给定两个节点代号，返回是否存在已确认的结构路径及其长度 |
| 查看者级只读 | `(space_id, viewer_account_id)` | terminology 目标的可选称谓词表 |
| 证据级只读 | `(space_id, fact_id)` | 某条已确认事实的类型/revision/端点代号 |

- **空间级工具是本设计中最需要评审的新授权形状**：现有 `agent_query` 的每个工具都以 `_resolve_scope(agent_session)` 取 actor，即 account-scoped。空间级工具不能复用该入口，必须新增一条**不经过 account** 的授权路径，且该路径必须：
  1. 只读；
  2. 只返回代号与结构，不返回姓名/自由文本；
  3. 对不可见节点 fail-closed（与 `get_profile_summary` 的 `FG_PROFILE_NOT_AVAILABLE` 同码防枚举）；
  4. 有输出上限（`enforce_output_limit`）。

### 9.3 注册表表达

- `ToolSpec.required_kind` 现为 `str | None`（`None` = 所有 runtime kind 可用）。改为显式三态：`"assistant"` / `"steward"` / `None`。
- `default_allowlist(kind=...)` 按 kind 生成：assistant 得到现有 8 个；steward 得到 steward 专用集。**两个集合不相交**，并加断言测试。
- `agent_tools.check_scope` 读 `StewardRun`/`AssistantExecution` 的 allowlist + kind，拒绝理由沿用 `AGENT_TOOL_SCOPE_DENIED`。
- `(run_id, tool_call_id)` 去重表（`agent_tool_calls`）对 steward 天然可用（FK 到 `agent_runs`）。**写类工具仍禁止注册**，红线不变。

### 9.4 Stage 3 的前置产出

独立 `design.md`，至少覆盖：工具清单与各自 scope、代号映射的稳定性（跨工具调用必须一致，否则模型无法引用）、输出上限、fail-closed 拒绝码、以及「工具返回的数据不得进入 `public_payload`」。

---

## 10. 出站与可观测性

### 10.1 `agent_provider_egress`

- **零改动**（D5）。`target_id` = child run id；`detail` 含 `provider_id`/`status`/`upstream_status`/`bytes_read`/`error_class`/`retryable`/`sent`/`header_ms`。
- Steward 因此**首次**获得：上游 4xx 保真（不再被折叠成可重试错误）、`x-should-retry` 下发、分层重试治理、流中断分类。

### 10.2 latency 端点（D11，同批发布硬约束）

- `GET /admin-api/v1/agent/latency` 新增 `kind` 参数（`assistant` 默认 / `steward`）。
- **默认只报 assistant**：不加 kind 过滤时把 steward run 混进 `model_turn`/`first_text` 会污染既有口径（两个种群的重试与耗时分布完全不同）。
- steward 视图复用同一聚合器（`_collect_metrics` 已按 `created_at` 选 run，加一个 `kind` 谓词即可）。
- `provider_retry` 由 `AuditLog.target_id in (run_ids)` 推导 → 自动只统计该 kind 的 run。
- `steward_model_calls.latency_ms` 的口径（含 timeout 删失样本）与 Pi 侧 `model_turn` **不得混算**；两者在报告中并列而非合并。

### 10.3 事件与 timing

- Steward child run 的事件类型子集：`run.started`、`turn.started`、`turn.completed`、`tool.execution.started`、`tool.execution.completed`、`run.compacted`、`run.settled`、`run.failed`、`run.cancelled`、`run.expired`。
- **禁止**：`message.user_added`、`message.assistant_added`、`assistant.text_delta`、`assistant.text_reset`（D9）。后端 `_validate_entry` 增加 fail-closed 分支。
- `timing_json` 复用（`source: "sidecar-v1"`）；Steward 的 `prepare`/`model_turn` 语义与 assistant 一致。
- **无 SSE**：Steward child run 的事件不向浏览器推送（无浏览器归属者）。SSE 端点因 §5.4 的 `agent_kind == 'assistant'` 条件天然拒绝。

---

## 11. 状态机与结算（无双终态）

### 11.1 状态映射

| 概念 | 载体 | 状态集 |
|---|---|---|
| 父作业 | `StewardJob` | `queued/leased/running/succeeded/failed/expired`（**不变**） |
| 辅助批次 | `StewardAssistBatch` | `pending/leased/applying/applied/failed/superseded`（**不变**） |
| 模型调用 attempt | `StewardModelCall` | `reserved/in_flight/succeeded/failed/degraded/unknown/skipped`（**不变**） |
| **child run 执行** | `agent_runs`（kind=steward）+ `steward_runs` | `queued/leased/running/succeeded/failed/cancelled/expired`（复用） |

### 11.2 attempt 与租约的关系（关键）

- **batch lease 是 Steward 的主租约**（`StewardAssistBatch.lease_owner` / `lease_until`），现状已如此。
- child run 的 lease（`agent_runs.lease_expires_at`）由 sidecar 心跳续期，**必须同时续租 batch lease**（同一立即事务）。否则两者会漂移：run 健康但 batch 过期 → 写回被拒。
- `StewardModelCall.attempt_no` 与 child run 的 `attempt` **不是同一个计数器**：run attempt 由 `lease_next` 递增（崩溃重试），`StewardModelCall` 是**每次发送一行**（唯一键 `(job_id, assist_kind, subject_key, input_hash, attempt_no)`）。映射：**一个 child run ↔ 恰好一行 `StewardModelCall`**（`steward_model_calls.run_id` UNIQUE 语义由索引保证；建议加 UNIQUE 索引）。

### 11.3 结算路径（单一领域服务）

```
sidecar POST /runs/{id}/settle
  └─ internal_agent: _authorize_steward_run → fence_steward_execution
     └─ agent_queue.settle_run(db, run, status=...)            # 落 run 终态 + 终态事件（复用）
        └─ if run.kind == 'steward':
             steward_assist.settle_child_run(db, run, ...)     # 同一事务
               ├─ StewardModelCall: in_flight → succeeded|failed|unknown|degraded（保守计费）
               ├─ 若 succeeded：封闭 schema 校验 → output_json
               ├─ _fence_check（写回栅栏：开关/policy/provider revision/证据/卡片/lease）
               └─ _apply_batch（CAS 应用产物）+ batch 终态
```

- **顺序硬约束**：`settle_run` 与 `settle_child_run` 必须在**同一事务**内提交，否则会出现「run 成功 / batch 未结算」的双终态。
- `agent_queue._settle` 的既有语义（`succeeded` 遇 `cancel_requested` 改判 `cancelled`；`failed` 原样保留）**必须保留**——取消不得吞掉真实故障。
- **父 job 终态 → child run 收敛**：`steward_pipeline.publish` / `record_failure` 在父 job 落终态时，若存在活跃 child run，置位其 `cancel_requested`（D6）。由 `steward_assist.recover_stuck_batches` 在下一个 tick 收敛为 `cancelled`。

### 11.4 崩溃恢复（第五个崩溃点）

现状四个崩溃点由 `recover_stuck_batches` 覆盖。新增**崩溃点⑤：child run 已创建、sidecar 崩溃**：

- 判定：`steward_runs` 存在 `agent_runs.status IN ('leased','running')` 且 `lease_expires_at < now` 的行。
- 收敛：run → `expired`（attempt 耗尽）或回 `queued` 重新可租（attempt 未耗尽）；对应 `StewardModelCall` 置 `unknown`（**保守计费、不自动重发**——上游未证实支持幂等键，与现状一致）。
- **不依赖 `agent_queue.reaper_pass`**（它选 `AgentJob`，Steward run 无 job → 天然不覆盖，见 §4.6）。
- 回归：模拟「child run 创建后 sidecar 被杀」，断言下一个维护 tick 内 run 与 attempt 都收敛，且无第二次出站。

### 11.5 无 SSE、无取消按钮

- Steward 无用户可见的「生成中」状态，因此**无** `/runs/{id}/cancel` 浏览器入口。
- 取消的唯一来源是父 job 终态与运维（admin rerun 会创建**新** job，不复活终态）。

---

## 12. 分批落地与回退

### Stage 1 — 骨架（零行为变化）

**交付**：迁移 `0055`；`RUNTIME_AGENT_KINDS` 扩展；`AssistantExecution`/`StewardExecution` + 两个 fence 函数；per-kind token claims；`/internal/agent/steward/jobs/lease`；`ContextOut` 可空字段；`_validate_entry` 消息类事件拒绝；浏览器/admin 面 kind 过滤；latency 端点 kind 参数；**`worker.ts` 多槽并发改造**（§7.2/§7.3）；`STEWARD_SYSTEM_PROMPT` 骨架 + prompt 版本上报端点；**空的 steward 工具集**（allowlist 为空 → Pi 以无工具单轮运行）。

**开关**：`STEWARD_PI_RUNTIME_ENABLED=0`（默认关）。

**验收**：
- 现有 backend/agent/frontend 全量检查通过（`ruff`/`mypy`/`pytest`；`type-check`/`lint`/`test`/`build`）。
- **行为等价断言**：四种 assist 仍走 in-process 路径；`steward_model_calls` 的 `run_id` 全为 NULL。
- 迁移隔离验证：临时 `DATA_DIR` 下 `upgrade head → downgrade → upgrade`。
- refusal guard 回归：构造冲突数据，断言 DDL 前中止。
- 新路径仅被测试驱动（一个受控 E2E：手工创建 batch → 租 child run → context → settle）。

**回退**：`FG_AGENT_ROLE=assistant`（sidecar 退回单 kind 单槽）+ `STEWARD_PI_RUNTIME_ENABLED=0`。无数据需要清理（child run 表为空）。迁移**不**回退（向后兼容的扩展）。

### Stage 2 — 迁移 `terminology` 一类

**交付**：terminology 的模型调用改走 child run（单轮、无工具、封闭 schema 校验与写回栅栏**不变**）。

**开关**：`STEWARD_ASSIST_TERMINOLOGY_VIA_PI=0`（默认关）。开时走 Pi，关时走现有 in-process。

**验收**：
- **差分测试**：同一批 terminology 输入，两条路径产出**等价**的 `output_json`（结构等价，非字节等价；prompt 文本不同）。
- egress 审计出现 `target_id = child run id` 的记录，且 `error_class`/`retryable`/`sent` 三字段齐全。
- 预算与计费等价：`billed_tokens` 在两条路径下的保守上界一致。
- 崩溃恢复：child run 卡住 → `unknown` 且不重发。
- 写回栅栏回归全绿。

**回退**：`STEWARD_ASSIST_TERMINOLOGY_VIA_PI=0`。在途 child run 由 §11.3 的父 job 终态收敛置位 + `recover_stuck_batches` 收敛。**孤儿断言**：回退后 `steward_runs` 中无 `leased/running` 行残留超过一个租约周期。

### Stage 3 — 工具化与其余三类

**前置**：独立 `design.md`（§9.4）。

**交付**：steward 只读工具集；`candidate`/`ranking`/`explanation` 逐个迁移（各自开关）。

**验收**：每个 kind 独立差分测试；工具越权矩阵（跨空间、不可见节点、未授权 viewer）；输出上限与 `public_payload` 洁净断言。

**回退**：逐 kind 开关回退到 in-process。

---

## 13. 风险与不可逆点

| 风险 | 面 | 缓解 |
|---|---|---|
| live SQLite CHECK 重建 | 迁移在真实库上重建 `agent_runs`（DROP/RENAME + 数据搬运） | refusal guard 先行；先在隔离 `DATA_DIR` 全链验证；备份先行（`python -m app.backup`） |
| `fence_execution` 泛化 | **安全关键**：拆分后任一函数漏一条检查即开后门 | 两个函数各有独立回归矩阵（membership 撤销 / lease 过期 / 取消 / attempt 不匹配 / 跨空间 / allowlist 漂移）；不允许共享「按 kind if 分流」的单函数 |
| Steward 授权判据从「人」变「作业」 | §5.2.1 的分层判据（已裁定接受） | 残留风险已如实登记；回归必须覆盖「撤权后 child run 在一个 tick 内收敛」与「移除任一检查则回归失败」 |
| 指标口径污染 | `admin_agent_latency` 混算两个种群 | D11 同批发布；默认只报 assistant |
| prompt 版本锚点失效 | `prompt_version()` 依赖服务端持有 prompt 文本 | §8.1 的版本上报 + fail-closed；两侧常量逐字断言 |
| **`worker.ts` 单槽→多槽改造（Q1=A2 引入，本设计最大风险项）** | **assistant 热路径**：`active` 单例假设贯穿 lease/heartbeat/abort/event buffer/cancel 收敛 | §7.2 逐点改造位置 + §7.2.1 跨槽回调 + §7.3 槽位预留；§7.4 四条新回归；`poll-scheduling.test.ts` 需重写；若回归面失控可回退 A3（§7.6） |
| 回退留孤儿 run | 在途 child run 在回退后无执行者 | §11.3 父 job 终态置位 + §11.4 崩溃点⑤收敛；Stage 2 的孤儿断言 |
| Stage 3 授权形状 | 空间级只读工具是新形状 | 独立 design + 越权矩阵回归 |

**不可逆点**：
1. 迁移 `0055` 一旦应用到有数据的环境，downgrade 会因 refusal guard 中止（`kind='steward'` 行存在时）。**这是刻意的**——不静默删除 child run 证据。
2. `ContextOut` 字段可空化是**协议变更**，两侧必须同批；顺序与 memory #453 同类：**backend 先于 sidecar**（sidecar 先升级会拒绝 `null` 字段）。回退顺序相反。

---

## 14. 部署顺序硬约束

**前进顺序**：`backend`（迁移 + 端点 + schema）→ `agent`（多槽改造 + steward 角色）→ 打开 `STEWARD_PI_RUNTIME_ENABLED` → 逐 kind 打开。

**回退顺序**：逐 kind 关闭 → `STEWARD_PI_RUNTIME_ENABLED=0` → `FG_AGENT_ROLE=assistant`（sidecar 退回单槽单 kind）→（如需）backend 回退。**不得**先回退 backend：新 sidecar 会对 `ContextOut` 的 `null` 字段与缺失端点报错。

> ⚠️ **Q1=A2 的额外部署约束**：sidecar 多槽改造会改 `worker.ts`，而 `agent` 与 `api` 是两个独立部署单元。多槽改造本身**向后兼容**（单 kind 时槽位退化为 1），因此可与 backend 同批；但**不得**先于 backend 部署——新 sidecar 会对 `ContextOut` 的 `null` 字段与缺失的 steward lease 端点报错。

**同批发布（不可拆分）**：
- `RUNTIME_AGENT_KINDS` 扩展 **与** `admin_agent_latency` 的 kind 过滤。
- `ContextOut` 可空化 **与** sidecar 的 kind 分支解码。
- `STEWARD_SYSTEM_PROMPT` **与** prompt 版本上报端点。

---

## 15. 与既有未闭合项的对应关系

| 未闭合项 | 出处 | 本设计如何闭合 |
|---|---|---|
| 为 Steward 建立独立 prompt/context/tool allowlist 合同 | 08-29 `implement.md:14`（未勾选） | §8.1 prompt 归属 + §8.2 context 正向校验 + §9 tool allowlist 不相交 |
| child run 与 StewardJob 一对一关联、继承 scope/policy/lease/取消、统一终态 | 08-29 `implement.md:16`（未勾选） | §4.4 `steward_runs` 关联 + §5.2 继承 + §11.3 单一结算 |
| 「若最终不启用 Pi，必须明确记录确定性内核为生产实现」 | 08-29 `notes.md` §6（未执行） | 本设计**启用** Pi，因此该待办由本任务取代；无论结论如何都必须在归档时显式记录（AC-1） |
| 「未来若引入 Pi Steward child run，必须另立任务」 | 09-01 `prd.md:64` | **本任务即该任务** |
| Steward 出站无 `agent_provider_egress` | 09-06 design B1 的副作用 | §10.1 D5 零改动复用 |
| Steward 无 run 级可观测性 | 同上 | §10.2/§10.3 |

---

## 16. 裁定结果（2026-09-25 用户已定，无开放项）

| # | 问题 | **裁定** | 影响面 |
|---|---|---|---|
| **Q1** | 进程拓扑 | **A2：改现有 sidecar 为多槽并发**（`FG_AGENT_ROLE=both`） | §7；**这是本设计最大的风险项**（动 assistant 热路径），§7.2 列出全部改造点与新增回归 |
| **Q2** | 执行边界 | **复用 `agent_runs` / `agent_run_events`** + 新增窄表 `steward_runs` | §2 D1、§4；收益：`provider_proxy` 零改动 |
| **Q3** | 迁移节奏 | **先迁 `terminology` 一类** | §12；一次迁完的回退面大得多 |
| **Q4** | 会话语义 | **无跨 run 历史**（单轮） | §8.3；有历史则去重键（`request_hash_for`/`last_checked_hash`）必须重设计 |
| **Q5** | 工具化 | **独立成 Stage 3**，不与迁移同批 | §9；空间级只读工具是新授权形状 |
| **Q6** | membership 替代判据 | **接受分层判据**（父 job 活跃 + 空间存在 + viewer 非空时该账号仍 active），残留风险如实登记于 §5.2.1 | 安全关键；收敛延迟窗口已接受，回归义务已写明 |

**裁定后的可回退点**：Q1 选 A2 若在实施中回归面失控，可回退到 A3（新增容器）——两者不冲突（§7.6）。A3 的 compose 方案保留在 §7.6 供需要时取用。
