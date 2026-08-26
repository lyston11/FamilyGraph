# Agent Runtime 规范（V2.1 起）

> 权威来源：`app/schemas/agent.py`（internal 协议与浏览器 API 的请求/响应形状）、`app/services/agent_events.py`（事件类型注册表）、`app/services/agent_tokens.py`（token 合同）。本文是执行摘要；**两侧实现冲突时以后端 schema 为准**。

## 1. 拓扑与信任边界（不可违反）

- 浏览器只访问 FastAPI `/api/agent/*`；sidecar 只访问 `/internal/agent/*`；用户 JWT 打 internal 一律拒绝，service/run token 打浏览器面一律无效。
- `agent/` sidecar 禁止：DB 驱动、fs 写盘工具、shell 执行、任意 HTTP 抓取依赖；compose 中无端口发布、无 /data 卷。
- feature flag `AGENT_RUNTIME_ENABLED` 默认关闭；关闭时三处路由（browser/internal/admin provider）均 503 `AGENT_RUNTIME_DISABLED`/`AGENT_DISABLED`。

## 2. Internal 协议合同（六端点）

| 端点 | 认证 | 请求 | 响应 |
|---|---|---|---|
| POST /internal/agent/jobs/lease | service token | `{leased_by, kind?, lease_ttl_seconds?}`（kind=None=任意队列，assistant FIFO 优先） | 200 平铺 `{job_id,run_id,agent_kind,attempt,tool_allowlist,policy_version,run_token}`；无可租 **204 空 body** |
| POST /internal/agent/jobs/{job_id}/heartbeat | run token | `{}` | `{ok:true, lease_expires_at, cancel_requested}` |
| GET /internal/agent/runs/{id}/context | run token | — | ContextOut（messages 为 `{id,role,content_json,created_at}`；provider.policy_result ∈ allowed/denied/denied_no_local/denied_cloud_forbidden） |
| POST /internal/agent/runs/{id}/events/append | run token | `{events:[{seq,type,public_payload}]}` | `{accepted:[{seq,event_id}], duplicates:[int]}`（(run_id,seq) 幂等） |
| POST /internal/agent/runs/{id}/tools/{tool}/execute | run token | `{version,input,tool_call_id?}` | `{ok:true, tool, version, output}` |
| POST /internal/agent/runs/{id}/settle | run token | `{status:"succeeded"\|"failed", error_code?, error?}`（**不接受 cancelled**——取消由服务端裁决） | SettleOut |

请求模型全部 `extra="forbid"`：新增字段必须两侧同步。

## 3. Token 合同

- HS256 JWT，共享密钥 `AGENT_SERVICE_SECRET`；**typ 必须逐字一致**：service=`"agent_service"`、run=`"agent_run"`（两端各自实现过一次 typ 漂移导致 401，教训见 §6）。
- run token claims 绑定 run_id/job_id/agent_kind/account_id/space_id/tool_allowlist，exp ≤600s；校验失败 fail-closed + audit `agent_internal_authz_denied`。
- 错误码常量一律引用 `app/errors.py`，禁止字符串字面量绕过（check 发现项）。

## 4. 执行模型不变式

- run+job 同事务入队；每 session 一个 active run、每账户 ≤2 assistant 并发、steward 每 space 一个 active job（partial unique index + 服务层预检）。
- 终态不可复活；lease 过期 reaper 收敛（回队重试→attempt 耗尽 expired；cancel_requested 直接 cancelled）。
- 事件先持久化再广播；(run_id, seq) 单调幂等；未知 type 拒绝不落公开流。新事件类型必须先在 `agent_events.EVENT_TYPES` 注册，sidecar 映射同步。
- **副作用工具红线**：服务端 (run_id, tool_call_id) 去重表 V2.4 才落地；在此之前禁止注册任何有副作用的工具（现有 echo/probe_scope 只读）。

## 5. Provider 治理

- platform_operator 经 `/api/admin/agent/providers` 管理（secret 只写不读，secretbox 密文落库）；空间设置 model 必须在该 provider allowlist 内。
- 策略在消息创建时前置门禁：非 allowed → 409 可解释错误（PROVIDER_UNRESOLVED / PROVIDER_LOCAL_REQUIRED_UNAVAILABLE），**绝不静默换云**。

## 6. Wrong vs Correct：双侧独立实现合同

### Wrong（V2.1 实际发生三次）
后端与 sidecar 各自按模糊描述实现，各自 mock 自测通过，compose 联调才暴露：
1. lease 请求 `{sidecar_id}` vs `{kind, leased_by}`；响应嵌套 `{job:{...}}` vs 平铺 LeaseOut。
2. token typ `"fg-agent-service"` vs `"agent_service"` → 全部 401。
3. settle body `error{code,message}` vs `error_code + error{...}`。

### Correct
1. 改协议先改 `app/schemas/agent.py`，sidecar 类型从 schema 抄写并在集成测试断言真实形状。
2. 共享字面量（typ、事件 type、policy_result 枚举）在一侧定义常量，另一侧测试逐字断言。
3. **每个涉及 internal 协议的任务，验收必须包含 compose 真实联调**（mock 不能证明合同）；本任务 E2E 链路：bootstrap→space→session→message(Idempotency-Key)→sidecar lease→context→settle→SSE 重放/Last-Event-ID 续传→幂等重放同 Run。

## 7. 测试要求（新增 agent 功能时）

- 并发约束冲突路径、终态不可复活、reaper 三分支收敛各有用例。
- token 篡改/过期/type 错用 → 401 + audit 行存在断言。
- SSE 断点续传无漏序、终态后连接关闭。
- Provider 矩阵五态 + secret 不回显。
- sidecar 侧：mock FastAPI 强制权威形状（严格校验请求体、204 空 body、ContextOut 归一化）。
