# Steward child run 心跳恒失败：job_id 取成字面量 "undefined"

## 目标与用户价值

修掉 steward child run 的租约续期：当前心跳**从未成功过一次**，任何超过一个租约周期（默认 120s）的 steward 模型调用都会被判失租并 abort，使长推理、terminology 多目标与上游变慢的情形静默失败。修复后 steward 与 assistant 一样靠心跳维持租约，长调用不再被误杀。

本任务由本轮状态核对中发现（用户问「当前管家 agent 是怎么样的了」）。

## 背景与证据边界

### 已确认事实

- `worker.ts:281` 用心跳续租：`.heartbeat(job.job_id, job.run_token, signal)`。
- `client.ts:525` 取 `job_id: String(raw["job_id"])`；而 **`StewardLeaseOut` 不含 `job_id`**（字段集：`agent_kind / assist_attempt_id / assist_kind / attempt / max_concurrent / policy_version / run_id / run_token / steward_job_id / tool_allowlist`）。`String(undefined)` → 字面量字符串 `"undefined"`。
- 可执行探针（`InternalClient` 直打本地 HTTP server）实测：`POST /internal/agent/jobs/undefined/heartbeat`。
- 服务端 `heartbeat_job` 首道判定 `claims["job_id"] != job_id` → 403 `AGENT_TOKEN_SCOPE_MISMATCH`；sidecar 把 `[401,403,409,410]` 视为租约失效 → `markLeaseLost` → abort。
- 数据印证：12 个 `expired` 的 steward run `heartbeat_at` **全为 NULL**，存活 122–125 秒（≈ `STEWARD_ASSIST_CALL_LEASE_SECONDS`=120）；`succeeded` 的 run `heartbeat_at` 非空。
- 覆盖缺口：`client.test.ts` 断言 steward 租约的 5 个字段但**没有 `job_id`**；`worker.integration.test.ts` 的 mock 只有 assistant 租约路由，steward 心跳从未被集成覆盖。

### 证据边界

- 12 次 `expired` 与心跳失败**时间吻合**且 `heartbeat_at` 为 NULL，但未逐条重放历史 run 的因果链；本任务修的是已由探针复现的机制缺陷，不改历史记录。
- 不影响 `succeeded` 的 53 个 run 的既有结论（它们在租约内完成）。

## 需求

### R1 心跳必须携带真实的 job 标识

steward child run 的心跳必须打到服务端可接受的目标，使续租成功。修复必须落在**协议字段**层，不得让 `client.ts` 出现 kind 分支（E2 合同：kind 差异只住 `agent/src/adapters/kind.ts`）。

### R2 两侧都要有断言，且断言真实值

- sidecar：断言 steward 租约的 `job_id`，并断言心跳 URL 含**真实 job id**（不是"发出了请求"）。
- backend：断言 steward 租约响应含 `job_id`。
- 不得只断言请求发生过——`"undefined"` 也能满足那种断言，这正是原缺口。

### R3 不改语义

`steward_job_id` 保留（授权根语义）；新增 `job_id` 是协议统一字段。无 schema、无迁移、无 wire 破坏。

## 验收标准

| ID | 可观察结果 | 对应需求 |
|---|---|---|
| AC-1 | steward 租约响应含 `job_id`，且等于 `steward_job_id`；sidecar 心跳 URL 为 `/internal/agent/jobs/<该 id>/heartbeat` | R1 |
| AC-2 | `client.ts` 与 `worker.ts` 仍无 kind 分支（现有结构性断言 `adapters-kind.test.ts` 继续通过） | R1 |
| AC-3 | 变异验证：去掉 `job_id` 字段后，sidecar 与 backend 的相关断言必须失败 | R2 |
| AC-4 | 开发环境实跑：至少一次 steward child run 出现 `heartbeat_at` 非空（续租真实发生），且不再新增因心跳失败产生的 `expired` | R1 |
| AC-5 | backend 全量 pytest / ruff / mypy 与 agent type-check / lint / test / build 通过 | R1–R3 |

## 不在范围

- 修改 `STEWARD_ASSIST_CALL_LEASE_SECONDS` 或租约时长语义。
- 回填历史 `expired` 记录或重放历史 run。
- assistant 侧心跳（其 `LeaseOut` 本就含 `job_id`，未受影响）。
- 生产环境操作；线上由用户手动发布。
