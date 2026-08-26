# V2.1 注记

- Pi 能注册任意自定义工具，但“能”不等于“应直接接 DB”；本项目锁定 FastAPI domain tool 边界。
- `session.subscribe` 只观察；需要拦截的 `tool_call/context` 必须使用 `pi.on`。
- `context` 每轮触发，重查询应由 ContextBuilder 预取，不在 hook 内执行。
- 持久化 SSE 借鉴 LearnGraph，但不复制其完整 workspace/sandbox/MCP 架构。
- 不静默 fallback 同时是隐私、成本和可复现性合同。

## 统一执行模型裁定（2026-08-26，主会话）

- `agent_runs` 是唯一执行记录，同时承载 assistant Run（绑定 session/message）与 steward 执行（绑定 job）；两者共用同一 FSM 列（status queued|leased|running|succeeded|failed|cancelled|expired、attempt/max_attempts、lease_expires_at、heartbeat_at、error_code/error_json、policy_version、tool_allowlist_json）。
- `agent_jobs` 是 durable queue 条目：每次执行创建时同事务插入 run+job（interactive 与 steward 都入队）；lease 只扫 jobs，返回配对的 run_id；heartbeat 打在 job 上同步更新 run；events/tools/settle 一律寻址 run_id。
- 并发约束：partial unique index 保证每 session 一个 active run、每空间一个 active steward job（status IN queued/leased/running）；每账户最多 2 个并发 assistant run 在服务层事务内校验。
- Internal 认证两级：①sidecar 静态 service 凭据（env 共享密钥签发的短 HMAC token）仅用于 lease；②lease 响应签发 run token（HMAC，claims 绑定 run_id、agent_kind、actor/space scope、tool allowlist、exp≤10min、jti），后续 context/events/tools/settle 只收 run token。用户 JWT 一律 403/401。
- 事件追加按 (run_id, seq) 幂等：重复 seq 返回 accepted=[] duplicates=[...]；未知事件类型/非法 payload 拒绝且不落公开流，写安全审计。
- 事件类型注册表（首版）：run.started、message.user_added、turn.started、turn.completed、message.assistant_added、tool.execution.started、tool.execution.completed、run.settled、run.failed、run.cancelled。card.* 为 V2.4 预留命名空间。
- Provider 配置表归后端：platform_operator 维护 provider 注册（kind=openai_compatible|local，base_url、密钥密文、allowlist 模型）与空间级选择/开关；context 端点向 sidecar 返回解析后的 {provider_id, model, policy_result}，密钥经 sidecar 安全配置解密，绝不出现在事件/日志/浏览器载荷。

## trellis-check 结论与遗留（2026-08-26）

PASS（非阻塞 7 项，#1/#2 已当场修复）。移交后续任务：

- **V2.4 前**：tool_call_id 目前仅落审计；首个写工具落地时必须同时实现 (run_id, tool_call_id) 服务端去重表——在此之前禁止注册任何有副作用工具（client.ts/worker.ts/tools.ts 注释已声明该约束）。
- **V2.2 待办**：`expired` 终态无对应公开事件类型（reaper 收敛的 run 只能靠连接关闭 + GET /runs/{id} 感知）；如 Assistant UI 需要显式终态事件需扩展注册表。
- **潜伏项**：后端注册表含 `familygraph.steward_ping`（min_kind=steward），sidecar 未注册它；steward 入队路径出现前无影响，届时需同步 sidecar 工具集。
- **部署注意**：compose 未限制 sidecar 对获准 Provider 以外的 egress（默认桥接全网可达）；design.md 已注明首版靠部署层限制内部网络，迁云时需在网络策略中落实。
