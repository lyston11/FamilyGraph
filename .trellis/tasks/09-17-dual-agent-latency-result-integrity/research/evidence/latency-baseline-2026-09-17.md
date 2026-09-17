# 双 Agent 延迟：源码与只读基线证据（2026-09-17）

基线 `main@62a2b15`。本轮仅本地只读检查与既有归档材料复核；未运行生产探针、未发模型请求、未改配置。

## 1. 管家：结果延迟保存窗口

`backend/app/services/steward_assist.py`

| 位置 | 事实 |
|---|---|
| `:1184` `execute_batch` | 文档自述阶段为 tx1 预发送 / HTTP 事务外 / tx2 审计 / tx3 写回 |
| `:1263` | `results: list[...]` 在函数局部累积，仅存内存 |
| `:1331`–`:1334` | `remaining = (batch.lease_until - utcnow())`；`timeout = min(STEWARD_ASSIST_TIMEOUT_SECONDS, remaining)` |
| `:1349` | `results.append((attempt, text, usage, exc, latency_ms, response_bytes))`——循环内只入内存 |
| `:1355`–`:1372` | 循环结束后才 `db.expire_all()` 并 `for attempt, text, usage, exc, latency_ms, response_bytes in results:`（`:1372`）统一结算；此处若租约已过期即返回 |

结论：返回结果与实际落库之间存在随整批剩余请求数增长的窗口。窗口内失租 → 恢复器把仍为 `in_flight` 的行置 `unknown`（见 §3）。这**只能证明存在“可能已返回却未保存”的机制窗口**，不能证明 09-17 那两笔上游一定返回过。

## 2. 管家：提前终止导致成功产物不应用

| 位置 | 事实 |
|---|---|
| `:1551` `_apply_batch` | 写回入口；`:1573` 先校验 `lease_until` 未过期 |
| `:1575` | `all_statuses` 汇总本批 attempt 状态 |
| `:1590` | `if "unknown" in all_statuses:` → `batch.status="failed"` 并 `return`，**在 `_fence_check` 之前** |
| `:1596` | `if "failed" in all_statuses:` 同样直接 `failed` 返回 |
| `:1753` `recover_stuck_batches`；`:1835` | `has_unknown = False` 起始；`in_flight → unknown`；`has_unknown → batch.status="failed"` |
| 恢复器 | `elif applied_products and batch.status=="applying"` → 才走恢复应用（`resume_apply`） |

结论：同批存在 `unknown` 时，`has_unknown` 分支先于 `applied_products`，已持久化成功的独立产物不会被恢复消费。要保全成果需同时改恢复器、应用器早退与终态聚合。

## 3. 管家：terminology durable 去重

`backend/app/services/steward_assist.py:283` `terminology_target_retryable`：

- 以 `space/viewer/root/target/semantic_hash` 匹配 batch `fence_json` 的 group/targets；
- `call.status in ("reserved","in_flight","unknown")` → `return False`（`unknown` 直接禁止）；
- `same_request` 且 `succeeded/degraded/failed`（非 `connect_failed`）→ `return False`；
- 只有 `failed+connect_failed` 或「`skipped` 且已预留」才进 `reservations`，且 `>=2` 或未满 60s 也不放行。

结论：`unknown` 的阻断早于 `request_hash` 比较，且不因新 job/prompt/request_hash/new generation 解除。这纠正了 09-17 归档中“改 request_hash 即可重试”的说法。

## 4. 管家：时限语义

| 位置 | 事实 |
|---|---|
| `:156` `_post_json` / `:167` | `httpx.Client(timeout=timeout)`——阶段/IO 等待限制 |
| `:1334` | 单次 timeout 取 `min(配置值, 剩余租约)`；配置默认见 `config.STEWARD_ASSIST_TIMEOUT_SECONDS` |

结论：`timeout` 不构成整笔墙钟上界（慢分块可反复重置 read 等待）。`timeout` 截断的调用在 admin 统计中是删失样本。

## 5. 助手：事件层只转完整消息

`agent/src/events.ts`

- `:178` `mapSessionEvent(event)`；
- `:186` `case "message_end"` 生成 assistant 消息；
- `:228` 注释明确 `message_update`/streaming deltas、`agent_end`、`agent_settled`、queue 等未映射。

结论：公共事件流不含增量正文；用户可见首字 ≥ 上游首块正文时间。是否为主要等待仍待测。

## 6. 助手：sidecar 默认参数（部署值待实测）

`agent/src/config.ts`（`readInt` 默认值，行为 `:106`–`:115`）：

- `:106` `AGENT_LEASE_POLL_MS` 2000
- `:107` `AGENT_PROVIDER_STREAM_MAX_RETRIES` 5
- `:109` `AGENT_DEFAULT_LEASE_MS` 60000
- `:110` `AGENT_EVENT_FLUSH_MS` 250；`:111` `AGENT_EVENT_BATCH_SIZE` 20
- `:115` `AGENT_REQUEST_TIMEOUT_MS` 15000

`agent/src/worker.ts`：`:71` 主循环、`:93` `leaseJob()`、`:116` 心跳 `setInterval`、`:282` 流期间启动事件 flusher、`:420`/`:436` 批量 flush。单实例串行处理 run。

这些是**代码默认**；生产以加载部署 env 后的实际值为准，本轮未核验实际生效值。

## 7. 既有观测能力与缺口

`backend/app/api/admin_agent_latency.py:1` 文档字符串明确：

- 管家：`steward_model_calls.latency_ms` 为逐笔真实耗时；`timeout` 为删失样本，分位数需结合 timeout 计数；
- 助手：只有 `agent_runs.created_at`/`settled_at`，「被租走」「首事件」**无直接时间戳**（`lease_expires_at` 被心跳持续前移），故不输出分段；需要分段应在 FSM 转换点补生命周期事件，而非为观测改运行时热路径。

结论：现有接口不能回答“首字多慢”，任何用总时长或续期字段代替的做法都是伪精度。

## 8. 历史结论纠正与边界

- 09-17 归档 `research/model-acceptance-2026-09-17.md` 记录 calls 21/22 为 `unknown`/`network_unknown` 且 `latency_ms` 缺失：这**不足以**还原上游是否返回，其归因“两笔上游均未返回”与“改 request_hash 即可重试”都应在新证据中修正。
- 09-15 归档 `prd.md`/`design.md`：实测 33s 在模型生成（上游中转）；指示在 +5.2s 消失而 run 到 +38.4s 结束；该 run 仅 2 turn / 6 条消息；`estimateTokens` chars/4 使中文压缩触发偏晚约 4 倍（当时无生产证据）。这是单次历史个例，不构成当前根因。
- `backend/app/models/steward.py`：`StewardModelCall` 状态机 `reserved → in_flight → succeeded|failed|degraded|unknown|skipped`，`unknown` = 无法证明上游未处理，保守计费且不自动重发；`StewardAssistBatch` 文档规定崩溃恢复按 attempt 状态收敛（`in_flight → unknown` 不自动重发，`succeeded+output_json` 重跑 fence 后 CAS 写回）。两者都待逐调用保全后复核语义。

## 9. 本轮未做

生产进程/配置/队列只读核对、真实模型调用、慢 chunk 复现、浏览器端到端验证、任何代码或配置变更。以上结论均来自本地源码与归档文档。
