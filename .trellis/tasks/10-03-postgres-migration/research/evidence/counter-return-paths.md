# 租约配额的归还路径盘点（2026-10-04）

## 核心风险：counter 成员资格 ≠ status

迁移到持久化 counter 后，正确性依赖一条容易被无声破坏的不变量：

> **每一条「进入活跃态」的边恰好 `+1`，每一条「离开活跃态」的边恰好 `-1`。**

现有代码通过直接赋值 `row.status = "..."` 改变状态（`app/` 中这类赋值有 50+ 处）。
只要有一条路径改了状态却忘了动 counter，名额就会**永久泄漏**——该空间/账户再也租不到，
而且**不会报错**。这正是本文件要固化的东西。

## 三个配额位置及其「活跃」定义

| 位置 | 配额语义 | 计入配额的 status | 配额来源 |
|---|---|---|---|
| `steward_assist.lease_attempt` | 每空间并发 assist 调用 | **仅 `in_flight`** | `STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE` |
| `steward.lease_next_steward_job` | 每空间 active job | `queued`/`leased`/`running` | `STEWARD_MAX_CONCURRENT_JOBS` |
| `agent_queue.lease_next` + `enqueue_run` | 每账户 assistant 并发 + 每 session 一个 active | `queued`/`leased`/`running` | `AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT` |

**注意三者语义不同**：assist 配额只数 `in_flight`（`reserved` 不算，因为它尚未发送）；
job/run 配额把 `queued` 也算进去（排队中已占用并发预算）。counter 的 `resource` 维度
必须区分它们，不能共用一个计数。

## StewardModelCall（assist 配额，仅 `in_flight`）

### 进入（恰好一处）

| 位置 | 说明 |
|---|---|
| `steward_assist.py:1427` `attempt.status = "in_flight"` | `lease_attempt` 中，紧接 `db.flush()` 之后返回 grant |

### 离开（`in_flight` → 非活跃）

| 位置 | 函数 | 目标状态 | 触发条件 |
|---|---|---|---|
| `steward_assist.py:1943` | `_settle_attempt` | `status`（succeeded/degraded） | 正常结算 |
| `steward_assist.py:1946` | `_settle_attempt_failure` | `failed` | 上游失败/超时/响应过大 |
| `steward_assist.py:1781` | `record_attempt_outcome` | `skipped` | 写回栅栏拒绝 |
| `steward_assist.py:2238` | `recover_stuck_attempts` | `unknown` | 崩溃点③：已发送但租约过期 |

**这四条都必须归还**。`record_attempt_outcome` 与 `_settle_attempt` 在同一事务内
（phase 1，调用方持锁），因此 counter 的 `-1` 也必须在这个事务里，否则会出现
「attempt 已终态但名额未归还」或反之。

### 不涉及 counter 的赋值（`reserved` 或已终态）

| 位置 | 原因 |
|---|---|
| `steward_assist.py:1395/1406/1420` | 发送门退休，此时**仍是 `reserved`**，从未 `+1` |
| `steward_assist.py:2338` `schedule_due_attempt` | 计划阶段，`reserved` |

**这是最容易写错的地方**：发送门的三个 `skipped` 与写回栅栏的 `skipped` 看起来一样，
但前者从未计入配额，后者必须归还。分类必须按「进入前的状态」而不是「目标状态」。

## StewardJob（每空间 active 配额，含 `queued`）

| 边 | 位置 | 说明 |
|---|---|---|
| +1（queued） | `steward.enqueue_steward_job` → `_enqueue_core_job_locked` | 插入即占用，**必须 `+1`** |
| queued→leased | `steward.py:650` | 状态变化但仍是活跃，**不 `±`** |
| leased→running | `agent_events._promote_to_running` 对应路径 | 同上，**不 `±`** |
| −1 | `steward.settle_steward_job`（`failed`/`superseded`） | 归还 |
| −1 | `steward.reaper_pass`（`superseded`） | 归还 |
| −1 | `steward_generations.mark_failed` 等 | 需逐条确认是否终态 |

**关键点**：`queued` 计入配额，所以 `+1` 发生在**入队**而不是**租约**。这与 assist
配额相反。若照搬 assist 的形态（在租约时 `+1`），每空间 active 上限会被静默放宽。

## AgentRun（每账户 + 每 session）

| 边 | 位置 |
|---|---|
| +1（queued） | `agent_queue.enqueue_run` → `_create_run_and_job` |
| queued→leased | `agent_queue.py:326` |
| leased→running | `agent_events.py:502` `_promote_to_running` |
| −1 | `agent_queue._settle`（`agent_queue.py:451`，`effective` 为终态） |
| −1 | `agent_queue.reaper_pass`（`agent_queue.py:619` `outcome`） |
| 无影响 | `agent_queue.prune_finished`（只删已是终态的行） |

**两个不同维度**：`AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT` 按 `account_id`，
「每 session 一个 active」按 `session_id`。counter 的 `scope_kind` 要能表达两者。

## 结构性守护（迁移前即可落地）

现有 `status` 赋值散落在 50+ 处，靠人工 review 无法保证「每条活跃态边都配了 counter 操作」。
因此需要一个**结构性测试**：

1. 扫描 `app/` 中所有对配额承载模型（`StewardModelCall`/`StewardJob`/`AgentRun`）
   `status` 属性的赋值与条件 `UPDATE`；
2. 要求每一处都出现在一张**显式分类表**中，分类为
   `enter` / `leave` / `internal`（活跃态内部转换）/ `pre_active`（尚未计入）；
3. 新增未分类的赋值即失败。

这样任何未来新增的状态转换路径都必须显式回答「是否影响配额」，而不是静默泄漏。

## 已落地的结构性守护

`tests/test_quota_state_transitions.py` 扫描四个配额相关文件里**全部**状态写入
（属性赋值 + 构造关键字），要求每一处都出现在显式分类表中：

| 分类 | 语义 |
|---|---|
| `enter` | 从非活跃进入活跃 → counter `+1` |
| `leave` | 从活跃进入非活跃 → counter `-1` |
| `internal` | 活跃态内部转换（如 `queued→leased`、reaper 回到 `queued`）→ 不动 |
| `pre_active` | 从未计入的状态出发（如发送门退休 `reserved`）→ 不动 |
| `post_active` | 已离开活跃态之后的改写 → 不动 |
| `not_quota` | 不属于配额承载模型（如 `StewardGeneration`）→ 不动 |

用例组：盘点基线（扫描面不得缩小）、分类表无失效项、**每一处状态写入都已分类**、
每个模型都有 enter 与 leave 配对、六种分类都被实际使用、发送门与写回栅栏的
`skipped` 被区分。

变异验证：

- 在 `lease_attempt` 插入一条未分类的 `attempt.status = "unknown"` → **2 个用例失败**。
- 把发送门（`pre_active`）错标成 `leave` → **用例失败**（会多归还名额、静默放宽上限）。

## 尚未完成

- 条件 `UPDATE` 形态（非 ORM 赋值，如 `sa.update(...).values(status=...)`）的状态
  变更尚未纳入扫描；当前扫描只覆盖属性赋值与构造关键字。
- 崩溃语义：counter 与 attempt 状态在同一事务内，因此进程崩溃不会造成不一致；
  但**跨事务**的恢复路径（`recover_stuck_attempts` 是独立事务）需要独立验证。
- `steward child run` 的 counter 分桶：设计结论是它**不消耗** assistant 的
  「每账户并发」配额（由 assist 的 per-space 配额治理），因此 counter 必须按 `kind`
  分桶，否则 steward 会挤占 assistant 的账户配额。此点已写入分类表注释，但尚未实现。
