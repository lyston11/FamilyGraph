# Agent Runtime 规范（V2.1 起）

> 权威来源：`app/schemas/agent.py`（internal 协议与浏览器 API 的请求/响应形状）、`app/services/agent_events.py`（事件类型注册表）、`app/services/agent_tokens.py`（token 合同）。本文是执行摘要；**两侧实现冲突时以后端 schema 为准**。

## 1. 拓扑与信任边界（不可违反）

- 浏览器只访问 FastAPI `/api/agent/*`；sidecar 只访问 `/internal/agent/*`；用户 JWT 打 internal 一律拒绝，service/run token 打浏览器面一律无效。
- `agent/` sidecar 禁止：DB 驱动、fs 写盘工具、shell 执行、任意 HTTP 抓取依赖；compose 中无端口发布、无 /data 卷。
- feature flag `AGENT_RUNTIME_ENABLED` 默认关闭；关闭时三处路由（browser/internal/admin provider）均 503 `AGENT_RUNTIME_DISABLED`/`AGENT_DISABLED`。

## 2. Internal 协议合同（六端点）

| 端点 | 认证 | 请求 | 响应 |
|---|---|---|---|
| POST /internal/agent/jobs/lease | service token | `{kind="assistant", leased_by, lease_ttl_seconds?}`；HTTP 端点只服务 Assistant sidecar；Steward 由 API maintenance canonical worker 直接调用确定性服务，不走该端点 | 200 平铺 `{job_id,run_id,agent_kind,attempt,tool_allowlist,policy_version,run_token}`；无可租 **204 空 body** |
| POST /internal/agent/jobs/{job_id}/heartbeat | run token | `{}` | `{ok:true, lease_expires_at, cancel_requested}` |
| GET /internal/agent/runs/{id}/context | run token | — | ContextOut（messages 为 `{id,role,content_json,created_at}`；provider.policy_result ∈ allowed/denied/denied_no_local/denied_cloud_forbidden；allowed 时 `base_url` 为**站内代理路径** `/internal/agent/runs/{id}/provider`，`api_key` 恒为 null——真实凭据/base_url 不出服务端） |
| POST /internal/agent/runs/{id}/provider/chat/completions 或 `/responses` | run token | 对应 Pi OpenAI adapter 的 JSON object body；空/非法/非 object 422 | ProviderGateway 代理（**唯一 egress**）：服务端 `resolve_runtime` 解密转发至已注册 Provider，成功流式透传 + `agent_provider_egress` 字节审计；上游错误一律 502 脱敏通用体，Run 非活跃或 `cancel_requested` 409，解析/解密失败 503 `AGENT_PROVIDER_PROXY_UNAVAILABLE`（fail-closed，绝不回退 sidecar env） |
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
- **取消门禁**：`cancel_requested` 是服务端权威状态。工具执行在 dispatch 前复核；ProviderGateway 在建立上游连接前及流式 chunk 边界复核，取消后拒绝/中断并记 failed egress audit。sidecar 的 AbortController/Pi `session.abort()` 只是加速路径，不能替代后端复核。

## 5. Provider 治理

- platform_operator 经 `/api/admin/agent/providers` 管理（secret 只写不读，secretbox 密文落库）；空间设置 model 必须在该 provider allowlist 内。
- 策略在消息创建时前置门禁：非 allowed → 409 可解释错误（PROVIDER_UNRESOLVED / PROVIDER_LOCAL_REQUIRED_UNAVAILABLE），**绝不静默换云**。
- P1 唯一 egress：sidecar 不持 api_key、不直连云端；模型请求经上表代理端点（run token 作 Bearer），`resolve_runtime` 为唯一解密出口。compose 中 agent 容器无外网（backend 网络 `internal:true`），外网 egress 仅 api 容器。sidecar 流重试经 `AGENT_PROVIDER_STREAM_MAX_RETRIES`/`_MAX_RETRY_DELAY_MS` 注入 pi-ai（5xx/408/409/429 指数退避）。
- Provider profile 首版固定为 `liu-dada/gpt-5.6-sol`（`openai-responses`、272000/60000、reasoning、text+image、low/medium/high/xhigh/max）；代码门禁拒绝其他云 profile，且不提供可由 Compose 环境变量关闭的绕过开关；local Provider 仍可作为本地敏感数据回退。

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

## 8. 只读领域工具层（V2.2 起）

- 六个 `familygraph.*@1` 查询工具实现在 `services/agent_query.py`，注册表 schema 在 `agent_tools.py`：input 一律 `additionalProperties:false`（拒绝任意 actor/space 字段），scope 只取 run token claims；注册表校验器递归执行 string/integer 的 min/max、enum，以及 array/items 边界。
- **投影口径**：`get_profile_summary` 复用 `visibility.evaluate(purpose=PURPOSE_AGENT) + payload_from_decision`，与 users 路由同一对原语；purpose=agent 上限 lineage_summary ≤ profile API，只紧不松。禁止在工具内重写可见性规则或返回 ORM 对象。
- **防枚举**：不存在与不可见同码 `FG_PROFILE_NOT_AVAILABLE`；关系路径 BFS 对不可见途经节点剪枝，不泄露存在性。
- **零写入**：查询工具纯 SELECT；新增写类工具前必须先落地 (run_id, tool_call_id) 去重表（见 §4 红线）。
- **双侧 schema 同步**：TypeBox 声明与后端 input_schema 必须逐字段同类型同名——实际发生过 cursor string vs integer 漂移；新增字段时两侧同改并加快照测试。
- **Provider wire tool names**：后端 registry、run token allowlist、内部执行路径和公开审计事件始终使用规范 `familygraph.*` 名称；由于部分 OpenAI-compatible relay 拒绝函数名中的 `.`, sidecar 仅在 Pi/pi-ai 的 provider 出站声明中将其映射为 `familygraph_*`（例如 `familygraph.list_visible_people` → `familygraph_list_visible_people`）。收到 Pi 的 tool hook/event 后必须反向映射回规范名再做 allowlist 校验、执行和事件投影；未知名称 fail-closed。该 wire 适配不得改变后端 schema 或审计合同。
- **前端 SSE**：EventSource 无法带 Authorization header，必须 fetch+ReadableStream 手工分帧解析；Last-Event-ID 续传、401→refresh→重连一次收口、终态事件后不再重连。错误码→文案映射表须覆盖后端可能返回的全部 agent 错误码（含 run.failed 载荷中的 POLICY_*/PROVIDER_*/SIDECAR_ERROR）。

## 9. Sidecar ContextOut 解码门禁（V2.7 修复）

ContextOut 是后端到 Pi session 的跨层安全边界，sidecar 不得用默认值“修复”畸形投影。
`InternalClient.getRunContext()` 在构造 session 前必须拒绝以下情况，并返回
`invalid_context_projection`：

- `run_id/session_id/account_id/space_id` 不是正整数；`agent_kind` 不是
  `assistant|steward`；`status` 不在后端 Run 状态枚举中；
- `attempt`、`next_event_seq` 不是非负整数，`policy_version` 为空或非字符串，
  `cancel_requested` 不是布尔值；
- `messages` 不是数组，或任一消息缺少整数 `id`、字符串 `role/created_at`、对象
  `content_json`；`tool_allowlist` 不是字符串数组；
- `context_blocks` 不是数组，或任一 block 缺少字符串
  `source_id/source_type/scope/sensitivity/citation/content`、非负整数
  `revision`。

Provider projection 的协议字段缺失/未知时必须保持未定义并由
`buildRunSession()` fail-closed；不得把 Responses profile 静默降级为
Completions。原始投影不写入日志或事件，错误只保留机器码和脱敏上下文。
Allowed runtime snapshot 还必须包含非空 `provider_revision`（由 Provider
`updated_at` 固化）；缺失或非法 revision 一律 `runtime_snapshot_invalid`，只对
明确标记的迁移前 legacy snapshot 走兼容分支。

### Wrong vs Correct

```text
Wrong: String(raw.run_id ?? "") / Boolean(raw.cancel_requested)
       → 后端契约破坏被伪装成可运行的 session。
Correct: 严格校验后才归一化；缺失或类型错误 → 502 invalid_context_projection。
```

### Required regression

至少覆盖一条缺失核心字段和一条 malformed context block；集成测试还要断言
畸形 projection 不会触发 Provider 请求或工具 dispatch。

Heartbeat 返回 401/403/409/410 时，sidecar 必须把 lease 视为已失效，立即 abort
Pi session 并跳过 settle；不能只把 410 当作 lease loss，否则 membership revoke
或服务端终态竞态会留下继续运行的模型流。
