# E 执行证据：助手网关错误分类与分层重试治理（2026-09-17）

基线：`main@8ae6414`（含 D 集成）。分支 `feat/09-17-assistant-retry-governance`，
worktree `~/PycharmProjects/fg-09-17-assistant-retry-governance`。
SDK 锁定：`@earendil-works/pi-ai` / `@earendil-works/pi-coding-agent` **0.84.3**（锁文件）。
全部验证用仓库测试夹具、本地 fake transport 与本地 `node:http` 假网关，
**未访问生产库、未调用真实模型、未部署**。

## 1. 修复前反例（`8ae6414`，定向红测）

| 反例 | 现象 | 根因 |
| --- | --- | --- |
| 永久上游错误被两层重试 | 400/401/403/404/422 统一转 502 → pi-ai 请求层重试 5 次、Pi 会话层再重试 3 次 | 网关把上游 >=400 一律折叠为可重试 5xx |
| 连接异常零审计 | `httpx.HTTPError` 分支只 `aclose()` 后抛 502，无 `agent_provider_egress` 行 | 审计只写在有响应对象的分支里 |
| 流中断/取消分类缺失 | 流中 `ReadError`、流中取消都落同一 `failed`，无法区分“上游可能已处理”与“服务端裁决” | 终态无分类字段，且取消路径无独立分支 |
| 未知重试语义 | 两层重试是否相乘无实测，只有“配置 5 次 + 默认 3 次”的推断 | 缺真实 SDK + 假上游对账 |

## 2. 实测（真实 SDK + 本地假网关）

`agent/test/retry-governance.test.ts`（10 例）驱动真实 `buildRunSession`，上游是本地
`node:http` 服务器，按 `provider_proxy.py` 的真实响应形状作答；请求层预算设为 2 次。

| 场景 | 实测出站请求数 | 会话层 `auto_retry_start` |
| --- | --- | --- |
| 永久 4xx 400/401/403（真实状态码 + `x-should-retry:false`） | **1** | 0 |
| 暂时 502（无停止头） | **12** = (2+1)×(3+1) | 3 |
| 503 + `x-should-retry:false` | **4** = (3+1) | 3 |
| 截断流（200 后无终止事件） | **4** = (3+1) | 3 |
| 一次失败后成功 | **2** | 0 |
| 会话层预算设为 0 | **3** = (2+1) | 0 |
| 本地策略拒绝（error 文本不含瞬态词） | **0** | 0 |
| 退避中 abort | 中止后请求数不再增长（< 3） | — |
| `SESSION_RETRY_BUDGET` vs SDK 默认 | 字段逐项相等（防 SDK 升级静默改变出站数） | — |

**关键机制**（决定了网关必须返回真实 4xx 而不是“502 + 停止头”）：
`x-should-retry:false` 只被 **pi-ai 请求层** `isRetryableProviderError` 读取；
Pi **会话层** `isRetryableAssistantError` 只看错误**文本**是否匹配瞬态词表
（`502`/`service.?unavailable`/`timeout`…）。因此 5xx + 停止头仍会让整轮重启 3 次
（上表第 3 行），而 4xx 的脱敏外壳不匹配瞬态词表，两层都不重试。

上游 401/403 是**上游**的状态，不是本 run 的 run-token：它只经 provider stream 报错，
不会走到 `worker.ts` 中处理 internal 401/403/409/410 的失租中止路径（该路径只读
heartbeat 与内部 API 错误），因此不会把上游凭据问题误变成 run 失租。

## 3. 实现

| 变更 | 位置 | 说明 |
| --- | --- | --- |
| `EgressFailure` 安全分类 | `services/provider_proxy.py` | `error_class`/`retryable`/`sent`；`sent=false` 仅由连接未建立证据得出 |
| 上游状态分类 | 同上 | 4xx 除 408/409/425/429 全为 `upstream_rejected`（不可重试）；其余 `upstream_transient` |
| 永久拒绝响应 | 同上 | 保留上游真实状态码 + `AGENT_PROVIDER_UPSTREAM_REJECTED` + `x-should-retry:false`；错误体仍脱敏 |
| 恰好一次审计 | 同上 | 连接异常、响应头失败、流中断、取消、成功各恰好一行；`blocked_by_policy` 标 `sent=false` |
| 流中分类 | 同上 | `ProviderProxyError`（取消/失租）→ `run_cancelled`；`httpx.HTTPError`/`GeneratorExit`/异常 → `stream_interrupted`，均 `sent=true` |
| 错误码注册 | `app/errors.py` | `AGENT_PROVIDER_UPSTREAM_REJECTED`（后端自己发出的码） |
| 头部下发 | `api/internal_agent.py` | 走既有 `raise_api_error(..., headers=...)` |
| 会话层预算显式化 | `agent/src/session.ts` | `SESSION_RETRY_BUDGET = {enabled:true, maxRetries:3, baseDelayMs:2000}`，显式声明不继承默认 |
| D 聚合一致性 | `api/admin_agent_latency.py` | `provider_retry` 只计 `retryable != false` 的失败；历史行无该字段时沿用旧 `failed` 口径 |

未改动：Provider/模型/档位选择、授权与既有预算数值、`AGENT_PROVIDER_STREAM_MAX_RETRIES=5`
与 `_MAX_RETRY_DELAY_MS=20000`、压缩恢复、`PROVIDER_EMPTY_ANSWER`、取消优先级、前端文案。

## 4. 后端受控场景（`tests/test_provider_proxy.py`，24 例）

| 场景 | 断言 |
| --- | --- |
| 上游 400/401/403/404/422 | 状态码原样透出、码 `AGENT_PROVIDER_UPSTREAM_REJECTED`、`x-should-retry:false`、上游错误体（含 `sk-…` 哨兵）不出现在响应与审计 |
| 上游 408/409/429/500/503 | 502 + `AGENT_PROVIDER_PROXY_UNAVAILABLE`、无停止头、`error_class=upstream_transient`、`retryable=true` |
| 连接失败 | 502、`transport_failure`、`retryable=true`、`sent=false`、恰好一行审计、异常原文（含内网地址）不透出 |
| 流中断 | 恰好一行、`stream_interrupted`、`sent=true`、`retryable=false`；中断前的块已透传 |
| 流中取消 | 恰好一行、`run_cancelled`、`sent=true`、零块透传（首块前复核即生效） |
| 策略阻断 | `blocked_by_policy`、`sent=false`、零上游请求 |
| 审计一致性 | `_egress_rows()` 断言每个场景 `len(rows) == 1`（不双记） |

D 聚合回归 `tests/test_admin_agent_latency.py::test_latency_metrics_excludes_non_retryable_failures`：
同一 run 内一次可重试 502 + 一次永久 401 + 一次取消 → `failed_attempts=1`、
`unmeasured_retries=1`、`retry_segments=0`（旧实现会造出长度 3 的虚假失败段）。

## 5. 门禁（已运行）

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd backend && .venv/bin/pytest -q          # 1691 passed, 3 skipped
cd agent && npm run lint && npm run type-check && npm test && npm run build   # 143 passed
```

## 6. 未完成 / 未授权

- **E-R3/E-R5 策略部分未决**：本轮只消除“永久错误被当可重试 5xx”的误重试并显式冻结现有
  两层预算数值；**未降低**总次数或等待上限。改变总预算需要实际请求数、费用与可用性取舍
  并单独批准（E-R5），故本任务不得标记全部完成。
- 未部署、未跑真实模型、未跑 API smoke 与浏览器链路（属 F/G）。
- `smoke`/`internal 真实联调`未执行：本地无完整双 listener 环境，保留给 F。
