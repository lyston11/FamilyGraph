# Steward 模型辅助：执行单元重构

> 状态：**重写版**（2026-09-25 第二轮）。第一轮设计（`git show ae96fc2`）在旧架构上做共存方案，
> 用户裁定否决：「不要为了保留当前的架构而搞得越来越复杂，我们需要使用清晰的链路和结构，需要重构旧深度重构」。
> 本文件是**唯一技术权威**，覆盖第一轮全部结论。

## 1. 目标

让 Steward 的模型辅助走**单一链路**，且**执行单元 = 一次模型调用**：

```
注册工作快照 → 逐 attempt 租约 → 载体执行 → 结算 attempt（含写回）
```

Assistant 与 Steward 共享**执行合同**（run 状态机 / lease / heartbeat / run token / context /
事件 / provider 网关 / egress 审计 / 取消门禁），但**不共享队列**（`agent_jobs` 保持 assistant-only）。

## 2. 现状的问题（为什么必须重构，不是共存）

### 2.1 执行单元选错：一次调用的状态被拆在 4 张表

```
StewardAssistBatch   分组/计划   status attempt lease_owner lease_until next_attempt_at fence_json error_code
StewardModelCall     账本        status attempt_no billed_tokens output_json run_id
StewardRun           执行绑定     run_id steward_job_id assist_batch_id fence_json
agent_runs           真实运行     status attempt lease_expires_at cancel_requested heartbeat_at
```

`_apply_batch` 的代码自己证明 **attempt 之间是独立的**：

```python
for attempt in attempts:
    product = attempt.output_json
    if not product:
        continue          # 部分成功可独立应用（B-R2）
```

唯一的跨 attempt 耦合是 fence，而 fence 的输入（`evidence_hash`、`policy_version`、provider 身份、
卡片 revision、terminology 语义哈希）**全是空间级**的，不是批次级。批次级 fence 只省了一次查询，
却把「一个 lease 管 4 种 kind × 最多 16 个 terminology 目标」这个不必要的耦合买了进来。

### 2.2 两条执行路径各维护全部 4 处

```
in-process:  schedule_due_batch → launch_batch → execute_batch(tx1/tx2/tx3) → _apply_batch
pi child:    lease_child_run   → sidecar      → settle_child_run         → _finish_batch_after_attempts
```

共用判定点被两条路径各调一遍：`_fence_check`(9 处)、`_reserve_due_attempts`(4)、`_apply_batch`(6)、
`_settle_attempt`(3)、`_budget_state`(3)。任何一侧改动都要在另一侧同步，否则静默漂移。

### 2.3 并发被压成全库 1，多空间互相阻塞

```python
STEWARD_ASSIST_MAX_CONCURRENT_BATCHES = 1          # 全库上限，不是 per-space
.where(StewardAssistBatch.status == "pending",     # 选批无 space_id 过滤
       StewardAssistBatch.next_attempt_at <= now)
```

20 个空间 → 同一时刻只有 **1** 个批次在跑。空间 B 等空间 A 跑完；A 内部一次慢调用阻塞 A 的其余
全部工作，并阻塞**所有**其他空间。

对照：assistant 是 **per-account** 槽位（每账户 ≤2），steward 核心 job 是全局容量 4 + per-space ≤1。
**只有 assist 这一层被压成全局 1**。

### 2.4 sidecar 的 kind 分支散落

`executeJob` 里 5 处 `if kind === "steward"`，把「run 生命周期」（与 kind 无关）和「会话语义」
（与 kind 有关）混在一个函数里。

## 3. 目标结构

### 3.1 两层，职责正交

```
StewardAssistPlan   （原 StewardAssistBatch）
  空间级工作快照：这次作业有哪些**可做的工作**、证据是什么。
  无租约、无执行状态、无 attempt 计数。
  ↓ 1:N
StewardModelCall    （执行单元）
  一次模型调用：自带 kind、投影、租约、状态、计费、产物、载体。
```

- **Plan 回答「该做什么」**：`kinds`、`evidence_hash`、卡片 id/revision、terminology 组与摘要、
  provider 身份快照。注册后**不可变**（证据变化由 fence 检出并放弃，不是改写 plan）。
- **Attempt 回答「谁在做、做到哪」**：`lease_owner` / `lease_until` / `next_attempt_at` /
  `status` / `carrier` / `output_json` / `billed_tokens`。

### 3.2 单一链路

```
1. plan_for_job(db, job)        核心提交确定性结果时登记 plan + 预留全部 attempt（reserved）
2. lease_attempt(db, *, space_id)  取一个到期 reserved attempt → in_flight，返回 (attempt, carrier)
3. carrier.execute(...)         载体执行（pi child run 或 in-process），HTTP 不在事务内
4. settle_attempt(db, attempt, result)
   ├─ 计费 + 状态（succeeded|failed|degraded|unknown|skipped）
   ├─ 封闭 schema 校验 → output_json
   ├─ fence 重验（该 attempt 的 kind）
   └─ CAS 应用产物
5. recover_stuck_attempts(db)   租约过期 → unknown（保守计费、不重发）
```

`_fence_check` 与 `_apply_product` 各**只有一处调用点**。载体是 attempt 的一个字段（`carrier`），
所以「逐 kind 迁移」= 换该 kind 的 carrier，**不需要批次内分流**。

### 3.3 并发按空间

```python
STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE = 2   # 每空间在途 attempt 上限
```

选 attempt 时按 `space_id` 过滤并计数。空间之间互不阻塞；同一空间内不同 kind 可并行。

## 4. 数据模型（迁移 `0055_steward_assist_execution_unit`，重写）

> S1 的 `0055_steward_child_run` **未发布**（生产 `steward_runs` 为空、`STEWARD_PI_RUNTIME_ENABLED=0`），
> 按用户裁定「全部重写」直接替换，不做 0056 收敛迁移。
> 沿用 0024/0038 的模式：**refusal guards 在任何 DDL 或 Alembic 版本移动之前执行**（memory #398）。

### 4.1 refusal guards（先于 DDL）

1. `agent_runs` 存在 `session_id IS NULL` 的行（新 CHECK 要求 assistant 必须有 session）。
2. `agent_runs` 存在 `kind NOT IN ('assistant','steward')` 或 `kind IS NULL`。
3. `agent_jobs` 存在 `kind != 'assistant'`。
4. `agent_sessions` 存在 `agent_kind != 'assistant'`。
5. `agent_runs` 存在 `job_id IS NOT NULL AND session_id IS NULL`。
6. `steward_model_calls` 存在 `status = 'in_flight'` 的行（lease 下移前不得有在途 attempt）。

### 4.2 `steward_assist_batches` → `steward_assist_plans`

重命名 + 收窄为纯快照：

```
保留：id, space_id, job_id (UNIQUE), evidence_hash, policy_version, fence_json, created_at
移除：status, attempt, next_attempt_at, lease_owner, lease_until, error_code, updated_at
```

- **移除执行状态**：`status`/`attempt`/`lease_owner`/`lease_until`/`next_attempt_at` 下移到 attempt。
- **移除 `error_code`**：错误属于 attempt，不属于快照。
- `job_id` 仍 UNIQUE（每 job 一个 plan）。
- 重命名用 `ALTER TABLE ... RENAME TO`（保留行与 FK）；SQLite 会自动改写引用该表的外键。

### 4.3 `steward_model_calls` 成为执行单元

新增（从 batch 下移 + 新增载体）：

```
lease_owner       VARCHAR(120) NULL
lease_until       DATETIME     NULL
next_attempt_at   DATETIME     NULL
carrier           VARCHAR(16)  NOT NULL DEFAULT 'inproc'  CHECK (carrier IN ('inproc','pi'))
plan_id           INTEGER      NULL FK steward_assist_plans(id) ON DELETE CASCADE
```

- `status` 增加 `leased` 语义？**不**。attempt 的 `reserved` 就是「已预留、可租」，
  `in_flight` 就是「已租、执行中」。`next_attempt_at` 表达退避。状态机**不变**：
  `reserved → in_flight → succeeded|failed|degraded|unknown|skipped`。
- `batch_id` 列**改名**为 `plan_id`（同一列，语义从「批次」变「计划快照」）。
- 保留全部既有唯一性：`uq_smc_job_kind_seq`、`uq_smc_attempt_key`、`uq_smc_run_id`。
- 新增 `INDEX ix_smc_due (status, next_attempt_at)`：租约选行用。

### 4.4 `steward_runs`（保留，收窄）

```
steward_runs
  id                 INTEGER PK
  run_id             INTEGER NOT NULL UNIQUE FK agent_runs(id) ON DELETE CASCADE
  steward_job_id     INTEGER NOT NULL      FK steward_jobs(id) ON DELETE CASCADE
  model_call_id      INTEGER NOT NULL UNIQUE FK steward_model_calls(id) ON DELETE CASCADE
  fence_json         JSON NOT NULL DEFAULT '{}'
  created_at         DATETIME NOT NULL
```

- **`assist_batch_id` / `assist_kind` / `viewer_account_id` 移除**：这三个都已在 attempt 上
  （`plan_id` / `assist_kind` / `viewer_account_id`），child run 只是 attempt 的**执行载体**。
- `model_call_id` **UNIQUE**：一个 child run ↔ 恰好一个 attempt（与 `uq_smc_run_id` 互为反向保证）。
- `ck_steward_runs_viewer` 随之移除（viewer 在 attempt 上，且既有校验在 attempt 层做）。

### 4.5 `agent_runs`：kind 扩展 + session 可空（同 S1）

```
kind        VARCHAR(16) NOT NULL  CHECK (kind IN ('assistant','steward'))
session_id  INTEGER               NULL
job_id      INTEGER               NULL
新增 CHECK  ck_agent_runs_scope_binding:
  (kind = 'assistant' AND session_id IS NOT NULL)
  OR
  (kind = 'steward'   AND session_id IS NULL AND job_id IS NULL)
```

保留 `uq_agent_runs_session_active`、`uq_agent_runs_job_id`、`ix_agent_runs_session_id`、
`first_leased_at`、`runtime_snapshot_json`、`cancel_requested`。

### 4.6 `agent_sessions` / `agent_jobs`：**不变**

`agent_kind = 'assistant'` / `kind = 'assistant'` 的 CHECK 保持。历史 `uq_agent_jobs_space_active`
**不恢复**（09-01 红线）。

### 4.7 downgrade 语义

- refusal guard 先行：存在 `kind='steward'` 的 run 行即中止（memory #399）。
- `steward_runs` drop、`steward_model_calls` 的 4 个新列 drop、`plan_id` 改回 `batch_id`、
  `steward_assist_plans` 改回 `steward_assist_batches` 并恢复执行状态列。
- 恢复 `agent_runs.session_id NOT NULL` + `kind = 'assistant'`，移除 `ck_agent_runs_scope_binding`。
- **用原生 `ALTER TABLE ... DROP COLUMN` 删列，不重建表**：重建会让 SQLAlchemy 的批量反射
  重新推导约束名，破坏更早迁移的 `drop_constraint`（S1 已踩过这个坑）。

## 5. 调度与租约

```python
def plan_for_job(db, *, job, facts_brief, visible, cards, prepared=None) -> StewardAssistPlan | None
    """核心提交后同一短事务：登记 plan（快照）+ 预留全部 attempt（reserved, next_attempt_at=now）。
    无网络。无可做工作 → None（与确定性基线等价）。"""

def lease_attempt(db, *, space_id: int, worker_id: str, ttl: int) -> LeasedAttempt | None
    """BEGIN IMMEDIATE：
       1. 计该空间在途 attempt（status='in_flight' AND lease_until > now）
       2. >= STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE → None
       3. 选该空间最早的 reserved 且 next_attempt_at <= now 的 attempt
       4. fence 重验（该 attempt 的 kind）
       5. 置 in_flight + lease_owner/lease_until，返回 (attempt, carrier)
    无网络。"""

def settle_attempt(db, *, attempt_id: int, result: AttemptResult, lease: LeaseIdentity) -> str | None
    """同一立即事务：
       1. 校验 lease 身份（lease_owner + lease_until 未过期）
       2. 计费 + 状态（unknown 保守计费）
       3. succeeded → 封闭 schema 校验 → output_json
       4. fence 重验（写回栅栏）→ CAS 应用产物
    返回 attempt 终态。"""

def recover_stuck_attempts(db, *, now=None) -> int
    """租约过期的 in_flight attempt → unknown（保守计费、不自动重发）。
    同时收敛已建但未结算的 child run（置 cancel_requested）。"""
```

- **`lease_attempt` 按 space 选行**：空间之间不互相阻塞。
- **载体是数据不是分支**：`LeasedAttempt.carrier ∈ {'inproc','pi'}`，由 kind 的注册开关决定，
  调用方按 carrier 分派执行器，不在调度层写 `if kind`。
- **fence 只在两处**：租约时（发送前）与结算时（写回前）。

## 6. 载体抽象

```python
class AssistCarrier(Protocol):
    carrier: str
    def execute(self, attempt: LeasedAttempt) -> AttemptResult: ...
```

| carrier | 实现 | 适用 |
|---|---|---|
| `inproc` | `steward_assist._post_json`（httpx + asyncio.timeout，既有实现） | 默认；S2 前全部 kind |
| `pi` | 受限 Pi child run：建 run + steward_run → sidecar 执行 → settle 回写 | 逐 kind 打开 |

- **逐 kind 迁移 = 换该 kind 的 carrier**。开关 `STEWARD_ASSIST_<KIND>_CARRIER=inproc|pi`。
- 两条 carrier 共用**同一套** attempt 状态机、计费、封闭 schema 校验、fence、CAS 应用。
  唯一差别是「谁发起 HTTP」。这是本重构的核心收益：不再有两条平行的执行路径。

## 7. 身份与授权（保留 S1 成果）

- `RuntimeAgentKind = Literal["assistant","steward"]`；`RUNTIME_AGENT_KINDS` / `QUEUE_AGENT_KINDS` /
  `SESSION_AGENT_KINDS` **三个常量**（S1 已建立，不得合并）。
- `AssistantExecution` / `StewardExecution` + `fence_assistant_execution` /
  `fence_steward_execution` **两个独立函数**（S1 已建立）。
- `fence_steward_execution` 的层级判据（已裁定，见 §7.1）**不变**，但绑定字段从
  `assist_batch_id` 改为 `model_call_id`。
- per-kind run token claims（S1 已建立）不变。

### 7.1 membership 判据（分层，已裁定 2026-09-25）

1. 父 `StewardJob` 是注册本 attempt 的作业且 `status='succeeded'`（授权根）。
2. 空间存在。
3. `viewer_account_id` 非空时（terminology），该账号对应用户仍是 active 成员。

**已接受的残留风险**（如实登记）：第 2 层是弱锚，不阻止已被撤权的用户数据在空间级被继续处理；
与 assistant 的差距是**收敛延迟**（一个维护 tick，而非下一次内部请求）。回归必须证明撤权在一个
tick 内收敛，且移除任一层会让回归失败。

## 8. 内部协议

| 端点 | steward 行为 |
|---|---|
| `POST /internal/agent/steward/attempts/lease` | **独立端点**；`STEWARD_ENABLED` AND `STEWARD_PI_RUNTIME_ENABLED` 双开关 503；无可租 204 |
| `POST /internal/agent/jobs/lease` | **保持 `kind="assistant"`** |
| `POST /jobs/{id}/heartbeat` | steward 分支同一立即事务续租 run 与 attempt lease |
| `GET /runs/{id}/context` | `session_id`/`account_id` 为 null、`messages: []`、带 `steward_prompt_version` |
| `POST /runs/{id}/events/append` | `run.kind == 'steward'` 时拒绝消息类事件（422） |
| `POST /runs/{id}/settle` | steward 分支经 `settle_run(on_settled=...)` **同事务**结算 attempt |
| `POST /runs/{id}/tools/{tool}/execute` | 复用；allowlist 按 kind |

**独立端点**的理由：让「哪个容器能租哪类作业」成为**路由级**约束，而不是 payload 校验。
**settle 同事务**的理由：分两次提交会在崩溃时留下「run succeeded / attempt 仍 in_flight」，即双终态。

## 9. sidecar 结构

### 9.1 拆开「run 生命周期」与「会话语义」

`executeJob` 里 5 处 `if kind` 全部消除，改为按 kind 取一个适配器：

```ts
export interface KindAdapter {
  readonly kind: AgentKind;
  readonly systemPrompt: string;
  /** Tools this kind may register (steward: empty in S1/S2). */
  toolNames(): string[];
  /** Prompt text handed to the model, from the projection. */
  buildPrompt(projection: RunContextProjection): string;
  /** Stable upstream cache key. */
  cacheKey(projection: RunContextProjection): string;
  /** Whether this kind emits message-class events (assistant) or reports its
   *  product through settle (steward). */
  readonly emitsMessageEvents: boolean;
  /** The model's product, extracted from the finished session. */
  extractResult(state: SessionOutcome): { text: string } | null;
}
```

- `executeJob` 只依赖 `KindAdapter`，**零 `if kind`**。
- 槽位模型（S1 已建立的多槽 Map、预留、按 run_id 定位）**保留**，它已被证明正确。
- `assistantAdapter` 包住现有 `ASSISTANT_SYSTEM_PROMPT` + `TOOL_VERSIONS` + 事件上报；
  `stewardAdapter` 用 `STEWARD_SYSTEM_PROMPT` + 空工具集 + settle 上报。

### 9.2 槽位与并发（保留 S1）

`FG_AGENT_ROLE`（`assistant|steward|both`）、`AGENT_MAX_CONCURRENT_RUNS`、
`STEWARD_ASSIST_MAX_CONCURRENT_CALLS`（与后端同名同值，lease 响应广播 `max_concurrent` 纠错）。

## 10. 状态机与结算

| 概念 | 载体 | 状态集 |
|---|---|---|
| 父作业 | `StewardJob` | `queued/leased/running/succeeded/failed/expired`（不变） |
| 工作快照 | `StewardAssistPlan` | **无状态**（不可变快照） |
| 执行单元 | `StewardModelCall` | `reserved/in_flight/succeeded/failed/degraded/unknown/skipped`（不变） |
| child run | `agent_runs`(kind=steward) | `queued/leased/running/succeeded/failed/cancelled/expired`（复用） |

- 状态机**语义不变**，只是 lease 从 plan 移到 attempt。
- `unknown` = 无法证明上游未处理 → 保守计费且**不自动重发**（memory #407）。
- 终态不可复活。
- 父 job 终态 → 置活跃 child run 的 `cancel_requested`；`recover_stuck_attempts` 在一个 tick 内收敛。

## 11. 崩溃恢复

| # | 崩溃点 | 收敛 |
|---|---|---|
| ① | core succeeded 无 plan | 补登 plan |
| ② | plan 已登记、attempt 未预留 | attempt 不存在，无残留 |
| ③ | attempt 已预留、发送前崩溃 | `reserved` 且 `next_attempt_at` 过期 → 可重新租 |
| ④ | 已发送、结算前崩溃 | `in_flight` + 租约过期 → `unknown`（保守计费、不重发） |
| ⑤ | child run 已建、sidecar 崩溃 | run `leased/running` + 租约过期 → `cancel_requested`；attempt 按 ④ 收敛 |

- ⑤ **不依赖 `agent_queue.reaper_pass`**（它选 `AgentJob`，steward run 无 job）。
- 回归：模拟「child run 创建后 sidecar 被杀」，断言下一 tick 内 run 与 attempt 都收敛且无第二次出站。

## 12. 回退

| 层 | 动作 |
|---|---|
| 逐 kind 载体 | `STEWARD_ASSIST_<KIND>_CARRIER=inproc` |
| Pi 运行时总开关 | `STEWARD_PI_RUNTIME_ENABLED=0` |
| sidecar 角色 | `FG_AGENT_ROLE=assistant` |
| 迁移 | **不回退**（downgrade 在有 steward run 行时按设计中止） |

部署顺序：backend 先于 sidecar。回退顺序相反。

## 13. 与第一轮设计的差异（本重构改了什么）

| 项 | 第一轮 | 本轮 |
|---|---|---|
| 执行单元 | `StewardAssistBatch`（含 lease/status） | `StewardModelCall`（attempt） |
| lease 位置 | batch | attempt |
| 并发 | 全库 `MAX_CONCURRENT_BATCHES=1` | **per-space** `MAX_CONCURRENT_CALLS_PER_SPACE=2` |
| 执行路径 | in-process 与 pi 两条平行 | **单一链路** + 可换 carrier |
| fence 调用点 | 9 处 | **2 处**（租约、结算） |
| 逐 kind 迁移 | 需批次内分流 | 换该 kind 的 carrier，天然共存 |
| sidecar | `executeJob` 内 5 处 `if kind` | `KindAdapter`，零 `if kind` |
| batch 表 | 执行状态机 | 降级为不可变 plan 快照 |
| `steward_runs` | `assist_batch_id`/`assist_kind`/`viewer_account_id` | `model_call_id`（UNIQUE） |

**保留的 S1 资产**：migration 0055 的 `agent_runs` kind 扩展与 `ck_agent_runs_scope_binding`、
三个 kind 常量、两个 fence 函数与安全矩阵、per-kind run token claims、sidecar 多槽模型、
prompt 版本跨层字面量、浏览器/admin 面隔离、latency kind 参数。
