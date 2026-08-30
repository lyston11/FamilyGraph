# Execution notes — V2 Agent Runtime Full Repair

## 2026-08-30 final implementation pass

本轮以代码和可复现实验为准收口，保留工作树中其他进程的前端 redesign 修改，不执行 reset/checkout，也不在任何文档、日志或测试输出中写入 API key。

### Runtime contract

- 运行时依赖为 `@earendil-works/pi-ai@0.84.3` 与 `@earendil-works/pi-coding-agent@0.84.3`；没有独立 `pi-sdk`。`pi-coding-agent` 持有 Assistant session/loop，`pi-ai` 持有 `openai-responses`/`openai-completions` 协议 adapter。
- 首版云 profile 固定为 `liu-dada / gpt-5.6-sol`：`openai-responses`、`https://api.liu-dada.com/v1`、reasoning=true、text+image、contextWindow=272000、maxTokens=60000、thinking levels=low/medium/high/xhigh/max。生产门禁由后端代码固定启用（不可由环境变量关闭）；local Provider 仍可用于本地敏感数据。
- Provider key 只在后端 ProviderGateway 解密并注入上游 Authorization。Context projection 仅包含 run-scoped internal gateway path、provider_name 和非敏感能力元数据；sidecar 不读取 Provider key、不直连外部 base URL。
- `provider_id` 是 DB/audit 数字标识，`provider_name` 是 Pi 注册语义；两者不再混用。Run 创建时保存 runtime snapshot，Provider 配置版本改变时 fail-closed。

### 本轮代码修复

1. Provider profile 在 admin 注册/更新、space resolution、runtime decrypt 三层校验；缺 key、坏密文、profile/API/model 不一致均拒绝。
2. sidecar 支持 `openai-responses` wire，真实 adapter stub 验证 `/v1/responses`、Bearer run token、`max_output_tokens=60000`、数组 input、无 `api_key`。
3. policy guard 增加递归 credential-key 检测，同时白名单保留 `max_tokens`、`max_completion_tokens`、`max_output_tokens`、`stream_options`、`include_usage` 等合法字段。
4. Provider proxy 对空 body、非法 JSON、非 object body 在创建上游 client 前返回 422；最终 outbound policy guard 在 egress 前执行。
5. 取消/lease 丢失贯通 worker → `AbortController` → Pi session abort → pi-ai stream signal；worker 不会在取消后 settle succeeded。后端工具执行和 Provider proxy 增加 server-authoritative `cancel_requested` 二次门禁；流式代理在 chunk 边界复核 run 状态，取消后中断并记 failed audit。
6. Provider 上游连接异常路径关闭 `httpx.AsyncClient`，避免连接池泄漏。
7. Provider proxy 将请求体绑定到 run snapshot：model 必须等于 `gpt-5.6-sol`，必须 `stream=true`，token cap 不得超过 60000；不合法请求在创建上游 client 前拒绝。
8. Assistant lease 明确请求 `kind=assistant`，Steward 只能由 maintenance canonical worker 消费；sidecar 收到 Steward job 直接拒绝。
9. Session 恢复当前作用域内的持久化 user/assistant 文本 transcript；事件 append 按 `(run_id, seq)` 幂等，`next_event_seq` 由服务端提供。
10. Compose/README 对 `SECRET_KEY` 与 `AGENT_SERVICE_SECRET` 使用强制注入、`.env` 权限 `0600`；Agent `/healthz` 是 liveness、`/readyz` 检查 FastAPI 可达性；web 有 healthcheck 且依赖 api healthy；Agent 无 published port。

### Verification evidence

| Gate | Command/result |
|---|---|
| Backend tests | `cd backend && .venv/bin/pytest -q` → **560 passed** |
| Backend lint/format/types | `.venv/bin/ruff check .` → pass；`.venv/bin/ruff format --check .` → 196 files formatted；`.venv/bin/python -m mypy app` → no issues (120 source files) |
| Agent | `npm run type-check` pass；`npm run lint` pass；`npm test` → **12 files / 78 tests passed**；`npm run build` pass |
| Frontend (dirty worktree) | `npm run type-check` pass；`npm run lint` pass；`npm test -- --run` → **37 files / 233 tests passed**；`npm run build` pass。该门禁包含其他进程的 redesign 修改，未回滚。 |
| Compose | `docker compose config --quiet` → pass；未输出展开后的 secret |
| Trellis | `python3 ./.trellis/scripts/task.py validate 08-30-v2-agent-runtime-full-repair` → `implement.jsonl 8/8`、`check.jsonl 5/5` |
| Targeted regressions | Provider profile/snapshot、credential-key guard、empty/non-object proxy body、cancelled tool/provider request、Responses wire、sidecar cancellation 均通过 |

### 未闭环证据（保持任务 in_progress）

- 尚未取得一次真实 `liu-dada/gpt-5.6-sol` 成功正文回显。此前其他中转上游出现的 `503 service_busy` 可由直连请求复现，属于上游容量状态，不是本地 Pi adapter 的协议错误；本轮不使用 guga 或任何 luna 模型作为运行配置。
- 因此 `AC-OPS` 的“真实 provider 成功 E2E”仍为 partial；stub wire、错误/取消路径和所有本地门禁均已完成。上游恢复后只需执行受控 E2E，并记录 provider/model/status/正文长度等脱敏证据，禁止记录 Authorization 或 key。
- Session transcript 当前按后端公开合同持久化 user/assistant 文本与 web citation；tool call/result 的 provider-private payload 不进入 AgentMessage。若未来要恢复工具结果，需单独设计受控摘要字段和隐私级别，不得直接把 Pi 私有消息写入公开事件。

### 安全备注

- `.env` 已设置为 `0600`；不要读取、复制或提交其内容。
- 文档和日志只记录 profile 元数据、状态码、字节数和测试结果；不记录任何 API key、run token 或密文。

## 2026-08-30 follow-up audit and repair addendum

本次复核确认运行时是 `pi-coding-agent`（session/agent loop）+ `pi-ai`
（`openai-responses` wire adapter），不是独立 `pi-sdk`。云模型固定使用
`liu-dada/gpt-5.6-sol` profile；`AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 已从
Compose 可覆盖配置中移除，后端代码固定启用门禁。

新增修复：denied runtime snapshot（包括无 Provider 的 `provider_id=null`）不可因
后续空间配置改变而复活；allowed snapshot 严格校验数值/枚举/容器字段；Provider
Gateway 和工具 dispatch 在出网/副作用前执行数据库 CAS cancellation fence；policy
guard 覆盖凭据 header 变体；事件 flusher 单 pump 严格按 seq 保序并保留失败 batch；
删除未使用的 `AGENT_CONTEXT_MESSAGE_LIMIT`，ContextOut 始终返回完整 transcript。

最新门禁：backend `pytest -q` **571 passed**，ruff/format/mypy（120 files）通过；
agent `npm test` **12 files / 80 tests passed**，type-check/lint/build 通过；frontend
`npm test -- --run` **40 files / 242 tests**，type-check/lint/build 通过；Compose
config、Trellis validate、git diff --check 通过。

唯一未闭环：尚未取得一次真实 `liu-dada/gpt-5.6-sol` 成功正文回显；任务保持
`in_progress`，不虚构外部 Provider 成功证据。
