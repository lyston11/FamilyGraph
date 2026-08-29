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
- [partial] **AC-CANCEL** — `agent/test/worker.integration.test.ts` 的 AbortController/Pi stream stub 通过；后端工具与 Provider proxy 增加 `cancel_requested` 门禁并有回归测试。真实上游中止尚未执行，待 liu-dada 可用后补脱敏 E2E。
- [x] **AC-PROXY** — body limit、空/非法/非 object 422、model/stream/token cap 与 run snapshot 绑定、最终 policy guard-before-egress、上游错误/断流审计与 AsyncClient 异常关闭均已实现；`backend/tests/test_provider_proxy.py` 相关用例通过。
- [x] **AC-AUTHZ** — `_authorize_run()` 每次 internal 请求核验 token scope、run/job/kind/account/space、active membership；internal 401/403/409/410 与审计回归通过。
- [x] **AC-QUEUE** — sidecar lease 固定 `kind=assistant`，Steward 由 maintenance canonical worker 消费；poison/steward boundary 回归通过。
- [x] **AC-SESSION** — ContextOut 按 session/id 顺序返回，sidecar 恢复 user/assistant 文本 transcript，event append `(run_id, seq)` 幂等且以 `next_event_seq` 接续；跨 scope 隔离测试通过。Pi provider-private tool payload 按合同不持久化。
- [x] **AC-GRAPH-TERMS** — graph 将 `space_context` 传入 VisibilityPolicy；TermRegistry personal > space > locale > system 反向解析与 raw input 保留测试通过。
- [x] **AC-TOOLS** — backend registry、sidecar TypeBox、internal schema 对齐；递归 string/integer/enum/array/items 校验和 `term_usage_consent` 门禁回归通过。
- [x] **AC-WEB** — approved token CAS 在事务提交后才发起网络请求；失败补偿/审计、SSRF/DNS pin、PII/secret gate 既有测试通过。
- [partial] **AC-OPS** — README/Compose secret、health/readiness、web healthcheck、Agent 无端口、`.env` 0600 已完成；`docker compose config --quiet` 通过。真实 `liu-dada/gpt-5.6-sol` 成功正文回显尚缺，不能虚构。
- [partial] **AC-GOV** — `task.py validate 08-30-v2-agent-runtime-full-repair`（implement 8/8、check 5/5）和全量门禁已通过；任务仍保持 `in_progress`，待真实 provider success evidence 后再归档。

### Final command log

```text
backend: pytest -q -> 560 passed
backend: ruff check / ruff format --check / mypy app -> all pass (196 formatted, 120 files typed)
agent: type-check / lint / npm test / build -> pass (12 files, 78 tests)
frontend (dirty worktree): type-check / lint / test / build -> pass (37 files, 233 tests)
compose: docker compose config --quiet -> pass
trellis: task.py validate 08-30-v2-agent-runtime-full-repair -> pass
```

### Closure rule

在没有真实 liu-dada 成功记录前，不把任务或 release gate 标为 completed；上游恢复后补一次不含密钥的成功/失败双路径记录，再更新本表和 `task.json`。
