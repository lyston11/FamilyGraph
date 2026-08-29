# V2 Agent Runtime Full Repair

## Goal

把 FamilyGraph 的 Agent 运行时收敛到已批准的 V2 合同：Assistant 使用 Pi coding-agent/pi-agent-core 与 pi-ai 模型协议，Steward 保持空间级确定性 worker；Provider、可见性、取消、会话历史、工具 schema 和运维证据在后端与 sidecar 之间形成可验证的单一边界。运行时默认安全失败，并使用本机 Pi 配置中的 `liu-dada / gpt-5.6-sol`（不记录或回显密钥）。

## Background and confirmed facts

- 真实依赖是 `@earendil-works/pi-ai@0.84.3`、`@earendil-works/pi-coding-agent@0.84.3`，没有独立 `pi-sdk` 包；模型 API 通过 `openai-completions`/`openai-responses` adapter 注册。
- 当前链路为 Browser → FastAPI public API → AgentRun/AgentJob → Node sidecar → Pi session → pi-agent-core tools → internal FastAPI protocol → ProviderGateway → upstream provider。
- Assistant 是 Pi session；Steward 目前是 Python `StewardEngine`/`StewardJob`，`steward_ping` 仅为骨架，不得被 Assistant sidecar 租用。
- 本机 Pi profile `liu-dada` 的非敏感模型配置为：base URL `https://api.liu-dada.com/v1`、API `openai-responses`、model `gpt-5.6-sol`、reasoning=true、text+image、context window 272000、max tokens 60000、thinking levels low/medium/high/xhigh/max。API key 只可作为服务端 secret 注入。
- 工作树已有其他进程的 `08-29-frontend-redesign` 未提交修改；本任务不得回滚、覆盖或重排这些文件。

## In scope

1. Provider profile/snapshot：将 `liu-dada/gpt-5.6-sol` 的 adapter、compat、reasoning、输入模态、上下文/输出上限和 thinking 映射纳入受控 Provider 配置；每个 Run 固化不可变 runtime snapshot，sidecar 只拿站内 proxy URL 和无密钥 projection。
2. 运行安全：取消/lease 丢失传播至 Pi prompt/provider stream；run token 每次请求实时校验 run 状态、空间成员关系和 scope；Provider body 大小限制、最终 policy guard、流中断/客户端中止 fail-closed 审计。
3. 队列边界：关闭/拒绝 generic steward queue 的 Assistant 租用路径；明确 deterministic Steward canonical job 的生产入口，防止 `agent_kind` 漂移。
4. 多轮会话：按 session/run 正确恢复历史，禁止每次只拼最新 user message 的全新内存会话；保留作用域与敏感等级。
5. 关系/可见性：Graph 将 `space_context` 传给单点 VisibilityPolicy；TermRegistry 支持 personal/space/locale/system 反向别名解析且保留原文。
6. 工具合同：后端 registry、sidecar TypeBox、internal schema 对齐；递归校验 min/max、minLength、enum、array/items 等；`record_term_usage` 需要服务端 consent/confirmed 门禁。
7. Controlled Web：approved token 在事务提交后才执行外部网络请求，失败回滚/审计清晰。
8. 部署/文档：修正 README/Compose secret、readiness/healthcheck、最小 secret 权限和 Agent egress 说明；补齐 Trellis AC 证据、测试和回归文档。

## Out of scope / deferred

- 不实现新的 Pi Steward child run、复杂多 Provider 路由或联网功能扩展；Steward 仍由确定性 worker 执行。
- 不迁移现有用户/空间数据（当前尚未部署，无成员）；不改动无关的前端 redesign 工作树。
- 不把平台管理员变成家庭数据主体，不放宽 V2 visibility、SourceFact 或 Agent 写入红线。

## Acceptance criteria

- [x] AC-PROVIDER：运行时只接受 allowlisted `liu-dada/gpt-5.6-sol` profile；api=`openai-responses`，272000/60000、reasoning、模态和 thinking map 与本机 Pi 配置一致；密钥不进入 projection、日志、事件或 sidecar 环境；Run context/provider 使用同一 snapshot。
- [partial] AC-CANCEL：用户取消、lease 过期、membership 撤销均使 Pi session/provider stream 在可观测超时内中止；不再产生后续重试/settle succeeded；有单测和真实 stub-stream 回归。真实上游中止尚待 liu-dada 可用时补证据。
- [x] AC-PROXY：Provider 请求体超过 `AGENT_PROVIDER_PROXY_MAX_BYTES` 在读取前拒绝；最终 `before_provider_request` policy 失败时请求不出网；上游错误、断流、客户端断开均 settle failed 并有脱敏 audit。
- [x] AC-AUTHZ：run token 绑定 run/job/kind/account/space；每次 internal context/provider/tool 请求检查 run active 与当前 active membership，撤权后返回 401/403/409 且写 audit。
- [x] AC-QUEUE：Assistant sidecar 只能租 `agent_kind=assistant`；Steward job 只能由 maintenance canonical worker 消费；毒药作业不会转成 Assistant 执行。
- [x] AC-SESSION：同一 session 的历史消息按后端顺序恢复并送入 Pi，跨用户/空间不可见；取消/重试不重复追加消息。
- [x] AC-GRAPH-TERMS：Graph visibility 调用包含 space_context；空间/个人别名可反解析，冲突按优先级收敛且 raw input 不变。
- [x] AC-TOOLS：三方工具 schema 快照逐字段一致；非法长度/数值/枚举/数组项 fail-closed；term usage 只有服务端确认 consent 才计数。
- [x] AC-WEB：approved token CAS 提交后再进行外部请求；网络失败不会留下已消费但未执行的 token；既有 SSRF/allowlist/PII 门禁不回退。
- [partial] AC-OPS：README 的干净环境启动说明能通过 Compose config；api/agent readiness 与 web healthcheck 语义一致；secret 文件权限受限；真实 stub provider 与 `liu-dada` 配置 E2E 记录成功和失败证据。真实 liu-dada 成功正文回显尚缺。
- [partial] AC-GOV：本任务 `task.py validate`、lint/type-check/test、AC 逐条 command/exit code/artifact 回写完成后才标记 completed；不得用“JSONL 可解析”冒充实现完成。所有本地门禁与证据已回写，任务因真实 provider success 缺失保持 in_progress。

## Risks and rollback

- Provider adapter 是最大兼容风险：先用本地 stub 验证 openai-responses 请求形状，再启用真实 profile；失败可回滚到 feature flag 关闭而不暴露密钥。
- 取消传播涉及 Pi API 版本差异：保留 AbortController 与 session abort 双路径，并以 stub 可观测请求作为回滚判据。
- schema/visibility 改动可能影响旧客户端；新增字段保持向后兼容，拒绝未知字段，必要时通过 flag 分阶段启用。

## Open questions

无阻塞问题。Pi Steward child run、联网扩展、多 Provider 路由列为后续任务。
