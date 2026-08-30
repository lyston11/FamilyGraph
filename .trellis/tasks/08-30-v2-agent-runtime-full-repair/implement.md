# Implementation plan

## Ordered checklist

1. **Evidence baseline and task governance**
   - Record current git status (preserve frontend redesign changes), package versions, and baseline backend/agent/frontend gates.
   - Update this file and `notes.md` append-only with command, exit code, and artifact for every AC.
2. **Provider profile/snapshot**
   - Inspect Pi `models.json` without copying secrets; add normalized `liu-dada/gpt-5.6-sol` profile and immutable run snapshot.
   - Make context, sidecar registration, and ProviderGateway consume the snapshot; reject profile/API mismatches.
3. **Run cancellation and live authorization**
   - Thread AbortSignal through worker → Pi session → pi-ai stream; call session abort and fail-closed settle on cancel/lease expiry.
   - Re-check active membership and run state on every internal endpoint; add revocation and disconnect tests.
4. **Provider Gateway hardening**
   - Enforce `AGENT_PROVIDER_PROXY_MAX_BYTES` while reading; invoke final policy guard before egress; classify upstream/stream/client errors and audit bytes/outcome.
5. **Queue kind isolation**
   - Restrict Assistant lease calls to `assistant`; reject steward jobs in sidecar; keep maintenance as the only Steward producer/consumer and test poison jobs.
6. **Session history**
   - Load persisted context messages in stable order and pass them to the Pi session; make event append idempotent and test multi-turn isolation.
7. **Visibility and terms**
   - Pass `space_context` through graph visibility; implement reverse TermRegistry alias lookup with precedence and raw-input preservation.
8. **Tool schemas and consent**
   - Align backend/TypeBox schemas and strengthen recursive validation; add persisted consent gate for term usage and regression tests.
9. **Controlled Web transaction boundary**
   - Commit token claim before network egress, add failure audit/retry semantics, and retain SSRF/PII gates.
10. **Compose, README, and secret hygiene**
    - Fix clean-start secret docs, health/readiness checks, permissions guidance, and egress claims; do not edit unrelated redesign files.
11. **Verification and closure**
    - Run backend `pytest`, `ruff check`, `ruff format --check`, `mypy app`; agent `npm test`, lint, type-check, build; frontend checks against dirty worktree noted separately.
    - Run Compose stub E2E and one real `liu-dada/gpt-5.6-sol` request if upstream is available; capture success and failure evidence without secrets.
    - Run `python3 ./.trellis/scripts/task.py validate 08-30-v2-agent-runtime-full-repair`; only then update task status and journal.

## Risky files / rollback points

- `agent/src/session.ts`, `agent/src/worker.ts`, `agent/src/client.ts`: Pi adapter, cancellation, lease kind and history.
- `backend/app/api/internal_agent.py`, `backend/app/services/provider_proxy.py`, `backend/app/services/agent_queue.py`: trust boundary and queue semantics.
- `backend/app/models/*`, migrations and `backend/app/services/visibility.py`, `intake_extractor.py`, `agent_tools.py`, `controlled_web.py`.
- `docker-compose.yml`, `README.md`: deployment contract.

Rollback is feature-flag disablement first; additive schema changes are safe because the system has no deployed members.

## Validation matrix

| AC | Evidence required |
|---|---|
| PROVIDER | profile unit test, snapshot test, stub wire capture, real request log with redacted provider/model/status |
| CANCEL/AUTHZ | cancellation + lease/membership revocation tests, internal 401/409 and audit assertions |
| PROXY | body-limit, guard-before-egress, stream-error/client-disconnect tests |
| QUEUE/SESSION | lease kind and poison job tests; two-turn history/isolation test |
| GRAPH/TERMS/TOOLS | visibility context, alias precedence, schema rejection, consent tests |
| WEB | token CAS/commit-before-egress test and existing SSRF/PII suite |
| OPS/GOV | compose config, healthcheck/readme checks, full test commands, `task.py validate`, AC table updates |

## Acceptance status and evidence (2026-08-30)

证据只引用可复现命令、测试或代码入口；不以“JSONL 可解析”替代实现证据。

- [x] **AC-PROVIDER** — `backend/app/services/agent_provider.py` 的标准 profile gate、snapshot 与 runtime fail-closed；`agent/test/responses-wire.test.ts` 验证 `liu-dada/gpt-5.6-sol` Responses wire；`backend/tests/test_agent_provider.py`、`test_agent_admin_providers.py`、`test_agent_schema_contract.py` 通过。projection 无 `api_key`，sidecar 仅使用 internal gateway path。
- [x] **AC-CANCEL** — `agent/test/worker.integration.test.ts` 的 AbortController/Pi stream stub 通过；后端工具与 Provider proxy 增加 `cancel_requested` 门禁并有回归测试。真实 liu-dada Run 14 在 leased/running 后取消，最终 `failed/PROVIDER_STREAM_ERROR` 且 `cancel_requested=true`，未 settle succeeded。
- [x] **AC-PROXY** — body limit、空/非法/非 object 422、model/stream/token cap 与 run snapshot 绑定、最终 policy guard-before-egress、上游错误/断流审计与 AsyncClient 异常关闭均已实现；`backend/tests/test_provider_proxy.py` 相关用例通过。
- [x] **AC-AUTHZ** — `_authorize_run()` 每次 internal 请求核验 token scope、run/job/kind/account/space、active membership；internal 401/403/409/410 与审计回归通过。
- [x] **AC-QUEUE** — sidecar lease 固定 `kind=assistant`，Steward 由 maintenance canonical worker 消费；poison/steward boundary 回归通过。
- [x] **AC-SESSION** — ContextOut 按 session/id 顺序返回，sidecar 恢复 user/assistant 文本 transcript，event append `(run_id, seq)` 幂等且以 `next_event_seq` 接续；跨 scope 隔离测试通过。Pi provider-private tool payload 按合同不持久化。
- [x] **AC-GRAPH-TERMS** — graph 将 `space_context` 传入 VisibilityPolicy；TermRegistry personal > space > locale > system 反向解析与 raw input 保留测试通过。
- [x] **AC-TOOLS** — backend registry、sidecar TypeBox、internal schema 对齐；递归 string/integer/enum/array/items 校验和 `term_usage_consent` 门禁回归通过。
- [x] **AC-WEB** — approved token CAS 在事务提交后才发起网络请求；失败补偿/审计、SSRF/DNS pin、PII/secret gate 既有测试通过。
- [partial] **AC-OPS** — README/Compose secret、health/readiness、web healthcheck、Agent 无端口、`.env` 0600 已完成；`docker compose config --quiet` 通过。真实 liu-dada Run 12 已成功并取得正文；但全量 backend/frontend 门禁当前被并行未提交任务的 8 个后端接口断言失败和 1 个前端语法错误阻断，需对应任务修复后再宣称全项目绿。
- [partial] **AC-GOV** — 本任务 Agent 与 Agent 相关 backend 回归、`task.py validate`、`git diff --check` 全绿，真实 success/cancel 证据已回写；整体任务仍保持 `in_progress`，因为工作树中并行 `08-30-space-manager-approval`/frontend redesign 改动尚未完成，不能把全项目门禁失败隐瞒为完成。

### Final command log

```text
backend: pytest -q -> 571 passed
backend: ruff check / ruff format --check / mypy app -> all pass (196 formatted, 120 files typed)
agent: type-check / lint / npm test / build -> pass (12 files, 80 tests)
frontend (dirty worktree): type-check / lint / test / build -> pass (40 files, 242 tests)
compose: docker compose config --quiet -> pass
trellis: task.py validate 08-30-v2-agent-runtime-full-repair -> pass
```

### Follow-up repair evidence (2026-08-30)

- Provider snapshot 回归覆盖 denied/no-provider 不可复活、布尔数值类型 fail-closed。
- Provider proxy/工具覆盖 CAS cancellation fence；HTTP lease 覆盖 Steward 隔离和
  route/API mismatch；policy guard 覆盖凭据 header 变体。
- Agent client/worker 覆盖 AbortSignal 中止重试、Provider key env 不读取、事件
  严格顺序重试；所有测试和门禁通过。
- `AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 不再由环境变量覆盖；Compose/README 已
  同步为代码固定门禁语义。

### Final gate recheck (2026-08-30)

- Agent：type-check、lint、**83 tests / 12 files**、build 全绿；集成测试在获准的
  本地回环权限下执行（受限沙箱的 `listen EPERM` 仅是环境限制）。
- Backend：**587 passed**；Agent 目标回归 72/72；ruff check、format（200 files）、
  mypy（121 source files）全绿。
- Frontend（并行 redesign 工作树）：**249 tests / 40 files**，type-check/lint/build
  全绿；未重排或回滚其改动。
- Compose、`git diff --check`、`task.py validate` 全绿。
- `InternalClient.normalizeRunContext` 现对核心字段与 context block 严格 fail-closed，
  malformed projection 统一为 `invalid_context_projection`；对应合同已写入
  `.trellis/spec/backend/agent-runtime.md` §9。
- Heartbeat 401/403/409/410 均视为 lease 失效，sidecar 立即 abort Pi stream；新增
  membership/授权撤销回归。
- Allowed runtime snapshot 强制非空 `provider_revision`，并新增缺失 revision 的
  fail-closed 回归。

本次复核没有取得真实 `liu-dada/gpt-5.6-sol` 的成功正文回显，故 AC-OPS、AC-CANCEL、
AC-GOV 仍为 partial，禁止更新为 completed 或归档。

### Closure rule

在没有真实 liu-dada 成功记录前，不把任务或 release gate 标为 completed；上游恢复后补一次不含密钥的成功/失败双路径记录，再更新本表和 `task.json`。

## Provider wire-name repair evidence (2026-08-30)

最小真实探针确认 liu-dada 的 502 根因是 OpenAI-compatible function name 中的
`.`：无工具请求、手工下划线工具名和 Pi adapter 生成的无工具请求返回 200；带
`familygraph.*` 工具名返回 502。修复保持后端规范合同不变，仅由 sidecar 在
Pi/pi-ai provider 出站 declaration 使用 `familygraph_*`，并在 policy/event hook
反解回 canonical 后再执行 allowlist、FastAPI dispatch 和公开事件。

- 代码：`agent/src/tools.ts` canonical↔wire 单一映射；`session.ts` 出站工具声明
  与 allowlist 使用 wire 名；`policy.ts`/`events.ts` 统一反解；所有 executor 和
  后端合同仍使用 canonical 名。
- 回归：Agent type-check、lint、build 通过，12 files/87 tests passed；新增
  wire declaration、policy allowlist、event/citation 反解测试；worker 集成测试以
  wire tool call 驱动并断言后端收到 canonical 名。
- 真实成功：Run 12 使用 `liu-dada/gpt-5.6-sol/openai-responses`，三次上游 egress
  均 200，Run succeeded，assistant 正文长度 13（`当前空间暂无我可见的人物。`），
  tool event 仍为 `familygraph.list_visible_people`。
- 真实取消：Run 14 同 profile 在运行后取消，cancel HTTP 200，最终
  `failed/PROVIDER_STREAM_ERROR`、`cancel_requested=true`，未出现 succeeded。

### Closure rule update

真实 Provider success 和 cancellation 证据已补齐，故 AC-CANCEL 的真实路径与
AC-OPS 的 Provider 成功证据不再缺失。任务状态暂保留 `in_progress`，仅因当前
工作树中另一并行任务的后端测试接口契约失败（579 passed/8 failed）和前端未提交
文件语法错误；不得把这些并行失败归因于本任务，也不得在归档前隐瞒它们。
