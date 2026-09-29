# 技术设计：steward 心跳的 job_id

## 1. 缺陷

`worker.ts:281` 用心跳续租：`.heartbeat(job.job_id, job.run_token, signal)`。

`job.job_id` 来自 `client.ts:525` 的 `job_id: String(raw["job_id"])`，而 **`StewardLeaseOut` 不含 `job_id` 字段**（字段集：`agent_kind / assist_attempt_id / assist_kind / attempt / max_concurrent / policy_version / run_id / run_token / steward_job_id / tool_allowlist`）。`String(undefined)` 得到**字面量字符串 `"undefined"`**。

可执行探针实测（`InternalClient` 直打本地 HTTP server）：

```
PROBE seen = ["POST /internal/agent/jobs/undefined/heartbeat"]
```

服务端 `heartbeat_job` 的第一道判定是 `claims["job_id"] != job_id` → 403 `AGENT_TOKEN_SCOPE_MISMATCH`。sidecar 的 `startHeartbeat` 把 `[401,403,409,410]` 视为租约失效，立即 `markLeaseLost` → abort。

### 数据印证

12 个 `expired` 的 steward run：`heartbeat_at` **全为 NULL**，存活时长 122–125 秒（≈ `STEWARD_ASSIST_CALL_LEASE_SECONDS` = 120）。对照：`succeeded` 的 run `heartbeat_at` 非空。即心跳**从未成功过一次**。

### 为什么没被测到

- `agent/test/client.test.ts` 断言了 steward 租约的 `steward_job_id` / `steward_attempt_id` / `assist_kind` / `max_concurrent` / `run_token`，**唯独没断言 `job_id`**；
- `agent/test/worker.integration.test.ts` 的 mock 只有 `/internal/agent/jobs/lease`（assistant）路由，没有 steward 租约路由，因此 steward 心跳从未被集成覆盖；它的心跳匹配用 `job.job_id`，`/jobs/undefined/heartbeat` 不匹配 → 401 → 被当作 lease lost。

### 实际影响

多数 steward 调用在一个租约周期内完成，所以 run 仍常成功；但**任何超过一个租约周期的调用都会被判失租**。长推理、terminology 多目标、上游变慢时都会中招——这正是 12 次 `expired` 的来源。

## 2. 方案：给 StewardLeaseOut 补 job_id

```python
class StewardLeaseOut(BaseModel):
    run_id: int
    job_id: int          # ← 新增：与 assistant 的 LeaseOut 同名同义
    steward_job_id: int  # 保留：语义仍是「授权根」
    ...
```

服务端 `lease_steward_attempt` 同时返回两者，都取 `grant["steward_job_id"]`。

### 为什么不改 client 去读 steward_job_id

那会让 `client.ts` 知道 kind 差异（"steward 读 steward_job_id"），而 E2 的设计明确要求**kind 差异只住在 `agent/src/adapters/kind.ts`**，`client.ts` 不得有 kind 分支。用 adapter 覆盖 `job_id` 也引入同样问题：`job_id` 是**协议字段**，不是 kind 语义差异——两个 lease 形状都应携带它。

### 为什么不删 steward_job_id

`steward_job_id` 承载「授权根」语义，`agent_tokens` 的 claims 与 fence 都用它。两个字段同值是刻意的：`job_id` 是协议统一字段，`steward_job_id` 是语义命名。

## 3. 兼容

- 纯加法字段：旧 sidecar 忽略未知字段（`Object.assign` 只覆盖已知键），新 sidecar 读它。
- 部署顺序无约束：后端加字段不破坏旧客户端；新客户端需要后端已加字段才拿到值——但旧客户端**本来就坏**（`"undefined"`），所以先部署后端是修复方向。
- 无 schema、无迁移、无 wire 破坏。

## 4. 测试要求

- `client.test.ts`：断言 steward 租约的 `job_id` 为字符串化的 `steward_job_id`（补上缺失的断言）。
- `worker.integration.test.ts`：给 mock 增加 steward 租约路由，并断言心跳打到 `/internal/agent/jobs/<steward_job_id>/heartbeat`（即覆盖「心跳 URL 用的是真实 job id」这一行为）。
- 变异验证：把 `StewardLeaseOut.job_id` 去掉（或让 client 取回 `raw["job_id"]` 的 undefined），上述测试必须失败。
- backend：`test_steward_child_run_acceptance.py` 或 `test_steward_pi_carrier_terminology.py` 断言租约响应含 `job_id`。

## 5. 风险

- 若只改 backend 不改 sidecar 测试，缺陷仍可能因 client 侧改动而回归 → 两侧都要有断言。
- 心跳 URL 的断言必须检查**真实 id 值**，不能只断言"发出了请求"——否则 `"undefined"` 也能通过（这正是原缺口）。
