# 双 Agent 响应延迟与管家调用结果保全 · 集成验收

父任务：`09-17-dual-agent-latency-result-integrity`
子任务：B `09-17-steward-attempt-result-integrity`（已归档）、A `09-17-assistant-latency-diagnosis`（已归档）
集成基线：`main@651ee07`（含两次子任务合并）

## 0. 一句话结论

**管家侧的系统自身缺陷已修复（结果不再因同批后续慢调用丢失，且有总时限）。助手侧已能准确
分段定位，证据指向上游模型生成，未做任何提速改动。** 用户感知的「两个 agent 都慢」中，
**管家被证实有真实程序缺陷并已消除；助手未发现程序缺陷，慢的成因是上游模型生成时间**，
因此**未解决**，且已明确列出需用户决定的产品选项。

## 1. 双链路阶段定义与样本

| 链路 | 阶段 | 真源 | 样本 |
|---|---|---|---|
| 助手 | `queue_wait` / `first_text` / `model_turn` / `tool_call` / `settle` / `runs_without_start` | `agent_run_events.created_at` + `run.started`/`turn.started`/`message.assistant_added`/`tool.execution.*`/`agent_runs.settled_at` | 生产只读 n=2 run / 4 turn |
| 管家 | 每笔 attempt 的 `latency_ms` 与安全终态（`succeeded`/`degraded`/`unknown`/`failed`） | `steward_model_calls` | 定向与全量回归；真实链路上次任务已跑 |

口径红线均已落实：`first_text` 每 run 一次、`model_turn` 逐轮采样、工具/心跳/`turn.started`
不冒充正文首字、无 `run.started` 计入 `runs_without_start`、缺失即 `n=0`/`null` 不零填充。

## 2. 管家（B）：已修复的系统缺陷

| 缺陷 | 修复 | 回归 |
|---|---|---|
| 结果存内存、循环后统一保存 → 后续慢请求耗尽租约时早先结果落不了库 | 逐笔 `_settle_attempt` 在自身短事务内重验 `(owner, attempt, lease_until)` 后结算并提交，再发下一笔 | `test_returned_result_is_persisted_before_the_next_send`（修复前红：第二笔发送时第一笔仍非终态） |
| `_apply_batch` 在业务栅栏前因同批 `unknown`/`failed` 早退 → 独立成功产物被丢弃 | 终态先定，再走写回栅栏，再应用独立产物；批次仍如实 `failed` | `test_partial_batch_applies_independent_product_and_stays_failed`（修复前红：`reason_text_llm` 计数 0） |
| 恢复器 `has_unknown` 早退 → 已持久化成果无法恢复应用 | 持久产物优先于 `has_unknown` 早退 | 混合批次恢复用例 |
| `httpx.Client(timeout=)` 是阶段限制而非整笔墙钟上界 → 慢分块可长期占用线程 | 读取循环内按 `monotonic()` 强制整笔截止，超界中止 | `test_slow_chunks_cannot_extend_past_the_total_deadline`（修复前红：读完 10000 块约 540s；修复后 0.3s 截止即停） |
| 合法空 `items` 被当作「未检查」可能反复发送 | 沿 `_mark_terminology_checked` 收敛 | `test_empty_terminology_result_is_marked_checked_and_not_resent` |

**边界未削弱**：unknown 保守计费且不自动重放；历史 unknown 不回填、不解锁；未调大超时/租约、
未换 Provider/模型、未提高并发；写事务内零外呼；失租旧执行者零结算。原「旧执行者不能补审计」
测试保留并加严（在飞接管场景 + `billed_tokens is None`）。

## 3. 助手（A）：已定位，未提速

| 阶段 | p50 | max | 判读 |
|---|---|---|---|
| `queue_wait` | 0.94s | 11.05s | 非主导 |
| `model_turn`（逐轮） | **33.15s** | 53.04s | **主导（但含重试，见下）** |
| `provider_retry`（逐段，下界） | — | 10.22s | 上游 5xx 不稳定造成 |
| `provider_failed_attempts` | 7 次 / 2 run | — | 每 run 都有 |
| `tool_call` | 0ms | 0ms | 非主导 |
| `settle` | 7ms | 10ms | 非主导 |
| delta → 可见（受控，真实 SDK） | = 剩余生成时间 | — | 发布时机所致 |

三项纠正（不掩盖旧结论）：

1. 旧文档称「助手无法分段、需补 FSM 生命周期事件」——**与数据不符**，已改写；
   分段真源是既有 `agent_run_events.created_at`，无需新增字段。
2. 「首段显示晚」的成因**已确认是发布时机**（`message_end` 才发布），不是 SSE 或渲染；
   已用真实 Pi SDK + fake stream 测出（上游 delta 与 SDK `message_update` 均到达，公共事件仍恰好 1 次）。
3. **`model_turn` 不是纯推理时间**（A-02）：上游 5xx 重试退避发生在同一轮内，已被计入。
   新增 `provider_retry`（下界）与 `provider_failed_attempts`（无歧义）把它单列：
   run 2 的 5 次 502 在同一轮内，该轮 33.15s 中 10.22s 是重试与退避，余 22.93s 含纯生成。
   **已知盲区**：每轮各一次失败的形态（run 1 两次 503 分属两轮）段长为 1，时长记 0，
   只能由失败尝试数看出有重试——不得用 `provider_retry.n` 反推「无重试」。

### 3.1 助手实际推理档位（本轮实测）

真实 Pi SDK + fake stream 探针（拦截 `fetch`、断言零 egress）捕获到
`streamSimple` 的 `options.reasoning = ["medium"]`：`session.ts` 不传 `thinkingLevel`，
`SettingsManager.inMemory()` 无默认档位，SDK 落到 `DEFAULT_THINKING_LEVEL="medium"`；
平台无档位控制项（`thinking_levels_json` 只声明 Provider 支持的档位列表）。
**这是可选的提速手段，但属质量取舍，本轮未改**。

## 4. AC 对照

| AC | 状态 | 依据 |
|---|---|---|
| AC-01 双链路阶段定义/样本/缺失标记/失败分母 | **部分** | 两条链路阶段与口径已定义并有真源；助手重试开销与推理档位已实测。受控场景矩阵（无工具/只读工具/多轮/压缩/排队/取消失租）**未做**；代理缓冲、时钟偏差未测 |
| AC-02 第一笔合法返回不因第二笔慢而退回 unknown | **通过** | 逐笔结算 + 回归（修复前红） |
| AC-03 混合批次安全消费独立产物、撤权拒绝、重复 recovery 幂等 | **通过** | 混合批次 + 既有栅栏/幂等回归 |
| AC-04 慢分块/时间耗尽/失租/中断的时限与资源有界 | **通过** | 总截止回归 + 失租栅栏回归 |
| AC-05 unknown 不重发、未发送失败有限重试、预算与四类校验保持 | **通过** | 既有回归全绿 + 逐笔结算未改重试语义 |
| AC-06 助手可解释主导阶段；做优化须证明阶段减少 | **部分** | 主导阶段已解释（模型生成 + 上游重试），并已实现 A-02 要求的重试/生成分离；**未做程序性提速**，故无「阶段减少」声明 |
| AC-07 真实小样本分别记录可达/调用/合法输出/应用 | **部分** | 助手真实只读 n=2（生产仅 2 run，已用尽）；管家真实链路上次任务已跑。未做真实模型压测对照 |
| AC-08 门禁/API smoke/浏览器/无泄露/迁移隔离 | **通过（有豁免）** | 后端 1649 passed / 3 skipped + ruff/format/mypy；agent 127 passed + lint/type-check/build；API smoke 56/56。**未跑**浏览器端到端（未改前端）。无迁移。 |
| AC-09 逐项写已解决/未解决、Spec/HANDOFF 同步、集成与清理 | **通过** | 本报告 + 两个子任务验收记录；Spec 已更新；worktree/分支已清理 |

## 5. 明确未解决

1. **用户感知的助手慢响应未解决**：成因是上游模型生成（p50 33.15s/轮），其中部分轮次还叠加
   上游 502/503 重试（实测单轮 10.22s 为下界）。本任务未做任何提速。
   不能因管家修复成功而视为整体完成。
2. **需用户决定的四项选项**（均未实施）：
   - 逐字/增量显示：成因已确认，但公开 delta 合同未冻结（消息 ID/顺序号/epoch、重连重放去重、
     终态替换、聚合频率、取消即停、引用绑定、滚动兼容）。
   - 更快模型或更低推理档位：当前实际档位为 SDK 默认 `medium`，降档改变质量与成本。
   - 上游稳定性：实测触发源是上游 502/503（不是退避上限）；可在网关层做更精细的
     重试/降级策略，但涉及可用性与成本权衡。
   - 并发/预算扩容：样本显示排队非主导，证据不支持该投入。
3. **受控场景矩阵与真实模型对照未做**：无足够新数据承诺「几秒响应」或百分比提速。
4. **既有 flaky（非本任务引入）**：`test_lease_expiring_while_actual_writeback_waits_for_sqlite_writer_is_not_adopted`
   在**未含本任务改动**的 `62a2b15` 检出上全量运行同样偶发失败，单独运行连续通过；该用例含真实
   墙钟 `sleep` 与 250ms 预算，全量运行 CPU/IO 争用会越界。未在范围内修改。

## 6. 集成检查

- 迁移：无（零 schema 变更）。
- 集成顺序：B 先合入并归档，A 随后（共享 backend 时严格串行，未并行）。
- API 合同：无变更（`/admin-api/v1/agent/latency` 仅新增响应字段 `assistant_phases` 内的
  `provider_retry` / `provider_failed_attempts` / `runs_with_provider_retry`）。
- 前端：未改动；`assistant_phases` 暂无消费方（纯只读观测）。
