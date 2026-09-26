# S1 Steward child run 骨架：迁移、身份、协议与 sidecar 多槽改造

> 父任务：[09-25-steward-pi-child-run-design](../09-25-steward-pi-child-run-design/prd.md)
> 权威设计：父任务的 `design.md`（§4 数据模型、§5 身份、§6 协议、§7 sidecar、§10 可观测性）。
> 状态：**Completed**（2026-09-25）。按父任务 `implement.md` §2 的 S1 清单执行完毕；实施记录见文末「实施结果」。
> 优先级 P0。**前置**：父任务设计已裁定（Q1–Q6，2026-09-25）。

## Goal

把「Steward 走 Pi child run」这条路**建成但不启用**：完成迁移、运行时身份拆分、内部协议扩展与 sidecar 多槽并发改造。S1 结束时，四种 assist 的行为必须与今天**逐字节等价**（仍走 in-process 路径），新路径只被测试驱动。

## 依赖

- 父任务 `09-25-steward-pi-child-run-design` 的 `design.md` 是唯一技术权威；本 PRD 只列范围与验收，不复制设计正文。
- 与 `09-11-steward-capability-followups` / `09-11-steward-cross-space-discovery` **无依赖**，且**不得**在本任务里顺手实现它们。
- `09-11-steward-assist-execution`（已归档）建立的 `StewardAssistBatch`/`StewardModelCall` 状态机是**保留资产**，本任务只新增执行载体，不改其状态机语义。

## Requirements

### R1 迁移 0055（`0055_steward_child_run`）

- **refusal guards 必须在任何 DDL 或 Alembic 版本移动之前执行**（父 design §4.1 五条），命中即中止并报告表/行/值，原库保持可恢复。
- `agent_runs`：`kind` CHECK 扩展为 `IN ('assistant','steward')`；`session_id` 改 nullable；新增 `ck_agent_runs_scope_binding`。
- 保留 `uq_agent_runs_session_active`、`uq_agent_runs_job_id`、`ix_agent_runs_session_id`、全部 FK 与 `runtime_snapshot_json` 等列。
- 新建 `steward_runs` 表（父 design §4.4）；`steward_model_calls` 加 `run_id`（`ON DELETE SET NULL`）+ 索引。
- `agent_sessions` 与 `agent_jobs` 的 `= 'assistant'` CHECK **保持不变**（队列红线）。
- downgrade：先跑 refusal guard（存在 `kind='steward'` 行即中止），再逆序恢复；**不得**破坏性删除已产生的 child run 证据行。

### R2 运行时类型与身份拆分

- `RuntimeAgentKind` 扩展为 `Literal["assistant","steward"]`；`RUNTIME_AGENT_KINDS` 同步。
- **`agent_sessions` 的 CHECK 保持 `= 'assistant'`**；新增 `models/steward.py::StewardRun`。
- `services/agent_execution.py`：拆为 `AssistantExecution` / `StewardExecution` / `Execution` union，以及 `fence_assistant_execution` / `fence_steward_execution` **两个独立函数**（各自完整检查集，父 design §5.2）。
- **队列 kind 白名单必须显式收窄**：`RUNTIME_AGENT_KINDS` 扩展后，任何直接用它做队列白名单的地方都会静默放开 steward。`agent_queue.py`、`agent_tools.py`、`agent_tokens.py` 三处必须改成显式常量 / per-kind 表，并加回归证明 steward 进不了 `agent_jobs`。
- `services/agent_tokens.py`：per-kind `_RUN_REQUIRED_CLAIMS`；`issue_run_token` / `decode_run_token` 按 kind 校验，逐 kind 断言 claims 全集。

### R3 内部协议扩展

- 新增 `POST /internal/agent/steward/jobs/lease`，`STEWARD_ENABLED` + `STEWARD_PI_RUNTIME_ENABLED` 双开关 503 门禁；**不放开既有 `/jobs/lease` 的 `Literal["assistant"]`**。
- `steward_assist.lease_child_run(...)`：选 batch → 预留 attempt → 建 run + steward_run → 返回 grant，**无网络调用**。
- `_authorize_run` 拆为 `_authorize_assistant_run` / `_authorize_steward_run`。
- `heartbeat`：steward 分支在同一立即事务内**同时续租 run 与 batch**。
- `ContextOut.session_id` / `account_id` 改可空（父 design §6.4）；steward 分支 `messages: []`。
- `_validate_entry`：`run.kind == 'steward'` 时**拒绝消息类事件**（`message.user_added` / `message.assistant_added` / `assistant.text_delta` / `assistant.text_reset`）。
- `settle` 端点：steward 分支调 `steward_assist.settle_child_run`，与 `settle_run` **同事务**。
- `context_builder.py` 的「steward 不得有 run_id」守卫改为**正向校验**（run_id 必须解析到 `steward_runs` 且 space 一致），不得删除该守卫。

### R4 Sidecar 多槽并发改造（父 design §7，**本任务最大风险项**）

- `config.ts`：`FG_AGENT_ROLE`（`assistant|steward|both`，缺失默认 `assistant`，**未知值 fail fast**）；`AGENT_MAX_CONCURRENT_RUNS`（默认 2）；`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（默认 1）。
- `client.ts`：`leaseJob(kind)` 按 kind 选端点与 body；`getRunContext()` 的严格解码按 `agent_kind` 分支。
- `worker.ts`：单槽 → `Map<string, Slot>`；**先预留槽位再 `await leaseJob()`**；`markLeaseLost`/`markCancelRequested` 按 `run_id` 定位（不改会导致跨槽取消被静默忽略）；`executeJob` 的 kind 校验按本槽 kind。
- **`STEWARD_ASSIST_MAX_CONCURRENT_BATCHES` 的两侧漂移必须消除**：`StewardLeaseOut` 新增 `max_concurrent`，sidecar 取 `max(本地, 服务端广播)`，只上调不下调。
- `session.ts`：`systemPrompt` 按 kind 选择（**不得复用 `ASSISTANT_SYSTEM_PROMPT`**）；prompt cache key 按 kind 派生（assistant 保持 `fg-${account_id}-${session_id}` 不变）。
- 新增 `prompts/steward.ts` 骨架 + 服务端 prompt 版本上报 + 两侧常量逐字断言。
- `tools.ts`：steward kind 的 allowlist 为空集；断言 assistant/steward 工具集**不相交**。

### R5 隔离与可观测性

- `api/agent.py`：`_own_session_or_404` / `_own_run_or_404` 加 `agent_kind == 'assistant'` **显式条件**（不依赖 `None` 比较的巧合）。
- `admin_read_model` / `AdminAgentRunOut`：`account_id` / `session_id` 改可空；admin 读模型**不得**暴露 Steward 的 `viewer_account_id` 或 prompt 内容。
- `admin_agent_latency`：新增 `kind` 参数，**默认 `assistant`**；必须与 kind 扩展**同批发布**。
- `agent_queue.prune_finished`：加 `AgentRun.kind == 'assistant'` 过滤。

### R6 部署与回退

- `docker-compose.yml` 的 `agent` 服务新增三个 env（**不新增容器**）；`STEWARD_PI_RUNTIME_ENABLED` 默认 `0`。
- **部署顺序**：backend 先于 sidecar（父 design §14）；回退顺序相反。
- 回退：`FG_AGENT_ROLE=assistant` + `STEWARD_PI_RUNTIME_ENABLED=0`；迁移**不回退**（向后兼容扩展，`steward_runs` 为空）。

### R7 范围边界

- **不启用**新路径：四种 assist 仍走 in-process；`steward_model_calls.run_id` 全为 NULL。
- 不实现 S2/S4 的 assist 迁移、不实现 S3 的工具、不删除 in-process 代码。
- 不接入真实模型做验收（用 fake provider / 受控 E2E）。

## Acceptance Criteria

- [x] **AC-1 行为等价**：四种 assist 仍走 in-process；`steward_model_calls.run_id` 全 NULL；现有 steward 测试**不改一行**通过。
- [x] **AC-2 迁移安全**：五条 refusal guard 各有回归（含「命中时 DDL 未执行」断言）；空库、合法存量、`upgrade → downgrade → upgrade` 往返全过（**临时 `DATA_DIR`**）。
- [x] **AC-3 队列红线**：steward kind 无法进入 `agent_jobs` / `agent_sessions`；`/jobs/lease` 仍拒非 assistant；三处白名单收窄各有回归。
- [x] **AC-4 身份拆分**：`fence_assistant_execution` / `fence_steward_execution` 各有一组独立安全矩阵（membership 撤销 / lease 过期 / 取消 / attempt 不匹配 / 跨空间 / allowlist 漂移）；**去掉任一检查则对应用例失败**。
- [x] **AC-5 协议合同**：`StewardLeaseOut` 形状被两侧逐字断言；`ContextOut` 可空字段按 kind 分支解码；steward 发消息类事件被 422 拒绝；settle 与 `settle_child_run` 同事务（构造中途失败断言整体回滚）。
- [x] **AC-6 context 守卫**：伪造 steward run_id（不存在 / 跨空间）→ 422；合法 steward_run → 通过。
- [x] **AC-7 多槽隔离**：父 design §7.4 四条回归全过——①两槽并发（用 `Promise.all` 真并发）互不串台；②steward 槽占满时 assistant 仍能在自己槽完成；③单槽 lease 丢失/取消不影响另一槽；④`leaseJob()` 连续抛异常后槽位不泄漏。
- [x] **AC-8 跨槽取消**：`markCancelRequested` 按 `run_id` 定位；回归证明「槽 A 取消不影响槽 B」且「槽 B 的取消信号不被丢弃」。
- [x] **AC-9 并发上限一致**：`max_concurrent` 广播生效（本地值小于服务端时上调）；两侧同名同默认有逐字断言。
- [x] **AC-10 prompt 隔离**：steward 使用 `STEWARD_SYSTEM_PROMPT`；两侧 prompt 版本常量逐字一致，不匹配时 fail-closed。
- [x] **AC-11 工具集隔离**：assistant/steward allowlist 不相交（断言）；steward 在 S1 为空集。
- [x] **AC-12 隔离与指标**：浏览器面不暴露 steward run（显式条件 + 回归）；latency 端点 `kind` 默认 `assistant`，steward 视图不污染 assistant 分布。
- [x] **AC-13 全量检查**：`backend` pytest/ruff/mypy、`frontend`/`system-admin-frontend`/`agent` 各自的 type-check/lint/test/build 全绿。
- [x] **AC-14 受控 E2E**：手工建 batch → `POST /internal/agent/steward/jobs/lease` → context → settle 全链路通过（**不接真实模型**）。
- [x] **AC-15 部署**（`docker compose config --quiet` 通过；真实 Compose 联调以真实 HTTP E2E 替代，见实施结果）：`docker compose config --quiet` 通过；Compose 真实联调按父 design §14 顺序执行并记录证据。
- [x] **AC-16 收尾**：`task.py validate` 通过；worktree/分支清理按 AGENTS.md 执行。

## Out Of Scope

- 任何 assist 从 in-process 迁移到 child run（S2/S4）。
- Steward 只读工具（S3）。
- 删除 in-process 辅助路径（S5）。
- 降低 assistant 侧重试预算。
- `09-11-steward-capability-followups` / `09-11-steward-cross-space-discovery` 的延期能力。

## Notes

- 父 design §7.8 是 `worker.ts` 改造的**参考实现**，实施时照此写；差异必须在 PR 里说明理由。
- `poll-scheduling.test.ts` 现有三个用例中两个会失效（父 design §7.4 逐条说明），**需要重写**，不是「重算期望值」。


---

## 实施结果（2026-09-25）

**分支**：`feat/09-25-steward-child-run-skeleton`（worktree `../fg-09-25-steward-child-run-skeleton`），12 个提交。

### 验证证据

| 检查 | 结果 |
|---|---|
| `backend` pytest | **1908 passed, 3 skipped**（连续 3 次全量一致） |
| `backend` ruff / ruff format / mypy | 全绿（`test_invitation_reachability.py` 的 2 条 lint 债为**预存在**，未触碰） |
| `agent` type-check / lint / test / build | 181 tests passed |
| `frontend` type-check / lint / test / build | 827 tests passed |
| `system-admin-frontend` type-check / lint / test / build | 127 tests passed |
| 迁移往返（隔离 `DATA_DIR`） | `upgrade → downgrade → upgrade` 通过 |
| `docker compose config --quiet` | 通过 |
| **真实 HTTP E2E** | 见下 |

### 真实 HTTP E2E（AC-14）

在 uvicorn internal listener（:18001，独立隔离库）上以真实 service/run token 驱动：

1. `POST /internal/agent/steward/jobs/lease` → 200，响应键为 `steward_job_id`/`assist_batch_id`/`assist_kind`/`max_concurrent`（**无** `job_id`）
2. `GET /runs/{id}/context` → 200，`session_id=null`、`account_id=null`、`messages=[]`、`steward_prompt_version=steward-v1`、`tool_allowlist=[]`
3. 消息类事件 → **422**（拒绝物化会话历史）
4. `run.started` → 200
5. `POST /runs/{id}/settle` → 200；**单次提交原子写入**：`agent_runs.status=succeeded`、`steward_model_calls.status=succeeded`（billed 3577 tokens、`output_json={"items":[]}`）、`steward_assist_batches.status=applying`
6. 重复 settle → **409**（终态不可复活）

开关门禁实测：`STEWARD_PI_RUNTIME_ENABLED=0` 时 lease 端点 503 `STEWARD_DISABLED`；`/jobs/lease` 传 `kind=steward` 被 schema 挡为 422；无 service token 401。

### 该 E2E 抓出的两个真实缺陷（已修）

1. `fence_steward_execution` 要求父 `StewardJob` 为 `leased/running`，但辅助批次只在确定性内核**成功后**登记（`_fence_check` 也以 `succeeded` 为门禁）→ 每个 child run 都会被自己的 fence 拒绝。父设计的 §5.2.1 措辞与登记流程不一致，已按代码事实改为 `succeeded`。
2. `append_events` 仍走 assistant-only 授权器，steward run（无 session/job）在消息事件守卫之前就被 403 拦掉。已改为按 kind 分派并复用同一个 steward fence。

### 由变异测试推导的安全矩阵（AC-4）

`tests/test_agent_execution_fence.py`（40 用例）不是「写完宣布通过」，而是逐条删除 fence 里的检查确认有用例失败。第一轮暴露了 3 个**因错误原因通过**的用例（viewer membership 查询在错误空间上也会失败，所以空间比较从未被真正执行）；修正后可隔离检查全部 CAUGHT，结构性不可证伪的（session scope 漂移被 immutability trigger 挡住、`job.run_id` 回链被 `run.job_id` 蕴含）在文件里**逐条注明原因**，不留静默未测的行。

### 与父设计的偏差（3 处，均已记录理由）

1. `steward_model_calls.run_id` 用 **UNIQUE** 索引（设计只说「加索引」）。普通索引下同一 run 可绑两行 attempt，结算无法判断结果属于哪一行。
2. `context_builds.account_id` 改为可空 + `ck_context_builds_account_binding`。设计称「无需改 context_builds」是错的：该列原本 `NOT NULL`，空间级 steward 投影根本无法记录。
3. 父 design §5.2.1 的父 job 状态由 `leased/running` 改为 `succeeded`（见上）。

### 已知未覆盖

- 真实 Compose 联调未执行（用户环境占用默认端口 8000/8001/8002/8080）。以隔离端口上的真实 HTTP E2E 替代，覆盖了协议、认证、状态码与原子结算，但**未覆盖**容器网络与 sidecar 实际租约。
- `test_steward_candidate_evidence_integration.py::test_lease_expiring_...` 为**预存在的 flaky**（在 pristine main 上 3 次中 2 次失败），与本任务无关。
