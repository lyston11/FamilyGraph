# FamilyGraph V2.1 Agent Runtime：Pi Sidecar 与安全工具协议

> 依赖：`08-26-v2-0-foundation` 完成并归档。

## Goal

在不让 Pi 直接接触业务数据库或浏览器凭据的前提下，建立可持久化、可恢复、可审计的 Node Agent sidecar、FastAPI Agent API、领域工具协议、ProviderGateway 与 SSE 运行骨架。

## Requirements

### RT-1 Sidecar 与 Pi

- 新建独立 Node/TypeScript `agent/` 服务，锁定 `pi-coding-agent` SDK 版本，不 fork 上游。
- 禁用/不注册 read、write、edit、bash 和任意 HTTP，只加载显式 FamilyGraph domain tool 与扩展工厂。
- 浏览器只访问 FastAPI；Agent sidecar 仅在内部网络可达，不持有用户 refresh token，不挂载 SQLite/uploads。

### RT-2 Session、Run、Event、Job

- FastAPI 持久化 `agent_sessions`、`agent_messages`、`agent_runs`、`agent_run_events`、`agent_jobs`。
- Session 固定 `account_id + space_id + agent_kind`；创建后不能改 scope。
- 一个 Session 同时一个 active Run；每账户最多两个 Assistant Run；Steward 队列按空间最多一个 active Job。
- Run/Job 有 queued/leased/running/succeeded/failed/cancelled/expired 及 lease、heartbeat、attempt、error、安全策略版本。

### RT-3 内部协议与工具

- sidecar 通过短期签名 service/run token 租赁 Job、取 Context、追加事件、调用领域工具；token 绑定 run_id、agent_kind、actor/space scope、tool allowlist、过期时间。
- 工具名、input/output schema 与 error code 版本化；未知工具/版本、额外字段、scope 不匹配一律拒绝。
- FastAPI 重新执行身份、VisibilityPolicy、领域命令、幂等与 audit；Pi Guard 放行不代表授权。
- 初始只提供测试/只读骨架工具，正式业务工具在 V2.2/V2.3 增加。

### RT-4 SSE 与幂等

- `agent_run_events` 使用每 Run 单调 event id/sequence，先持久化再广播；支持 `Last-Event-ID` 或 `after_event_id` 重放。
- 消息创建接受 Idempotency-Key；相同 key+scope+payload 返回原 Run，payload 不同返回冲突。
- 浏览器断线、FastAPI 重启、sidecar 重试不得重新执行已完成副作用工具。
- SSE 包含 message/turn/tool/run/card 生命周期所需结构，不把敏感 prompt、密钥或未脱敏工具结果原样广播。

### RT-5 Provider

- 支持一个 OpenAI-compatible 云 Provider 与一个可选本地 Provider；平台运营者管理密钥和 allowlist，空间管理员只选允许的模型/开关。
- ProviderGateway 统一能力探测、超时、错误映射、usage 与审计；不得静默 fallback。
- Policy 决定 cloud_allowed/local_required/denied；本地要求但本地不可用时可解释拒绝。

### RT-6 可运维性

- 健康检查分别覆盖 process、FastAPI connectivity、Provider readiness；Provider 不可用不应让 v1 API 停服。
- Run 取消、超时、sidecar crash、lease 过期、重复事件和非法事件均有明确恢复语义。
- Agent 能力由服务端 feature flag 总开关控制，默认可整体关闭。

## Acceptance Criteria

- [ ] AC-RT1：sidecar 在无 DB/data volume、无 coding tools 情况下运行，网络面只包含 FastAPI 与获准 Provider。
- [ ] AC-RT2：Session scope 不可变；并发 Run/用户限额/Steward 空间队列在并发测试中成立。
- [ ] AC-RT3：篡改 run token、tool name/version/scope/schema 均 fail closed 且写安全审计。
- [ ] AC-RT4：SSE 断线后按 Last-Event-ID 完整重放，无漏序、乱序或工具重执行。
- [ ] AC-RT5：重复 Idempotency-Key 同 payload 返回同 Run，不同 payload 返回 409。
- [ ] AC-RT6：Provider 云/本地选择、敏感强制本地、无本地拒绝、无静默 fallback 有集成测试。
- [ ] AC-RT7：sidecar crash/lease expiry 可恢复，已提交的副作用不会重复；纯推理可按 attempt 策略重试。
- [ ] AC-RT8：Node type-check/lint/unit/integration/build、后端测试、Compose health 全通过。

## Out Of Scope

- 不交付最终 Assistant UI、亲属推理、Steward、Memory/RAG 或联网工具。
- 不接任意 SQL、shell、文件、MCP；不 fork Pi。

## Blocking Open Questions

无；具体 SDK 小版本在实现时按测试锁定，不改变协议。
