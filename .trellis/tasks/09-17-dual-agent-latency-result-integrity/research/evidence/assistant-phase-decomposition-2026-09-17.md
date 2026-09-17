# A 证据：助手耗时分段（生产真实数据 + 可复核实现）

基线 `main@45f636a`。本文记录 A 子任务的取证与实现结果，供父任务集成验收引用。

## 1. 关键更正：既有结论「无法分段」是错的

`app/api/admin_agent_latency.py` 原 docstring 声称助手侧只能给出「入队 → 终态」总时长，
因为「被租走时刻/首事件未落库、应通过 FSM 转换点补生命周期事件」。**该说法与生产数据不符。**

`agent_run_events` 表（迁移已在生产库）为每个事件持久化 `created_at`，且事件类型本身
就是阶段边界：

| 阶段边界 | 事件类型 |
| --- | --- |
| 入队 | `agent_runs.created_at` |
| 取得执行权 | `run.started`（sidecar 在 lease → running 转换点写入） |
| 每轮模型开始 | `turn.started` |
| 每轮正文落地 | `message.assistant_added`（仅非空正文） |
| 工具 | `tool.execution.started` / `tool.execution.completed`（按 `tool_call_id` 配对） |
| 终态 | `agent_runs.settled_at` |

因此**无需新增字段、无需 schema 变更、无需改热路径**即可拆解。原 docstring 的
「应通过 FSM 转换点补生命周期事件」建议是多余工作，已改写为事实描述。

## 2. 实现（`app/api/admin_agent_latency.py`）

新增 `assistant_phases` 段，由持久事件时间戳推导，全部为只读查询（不触发模型）：

| 字段 | 定义 |
| --- | --- |
| `queue_wait` | `run.created_at` → 该 run 最早的 `run.started` |
| `first_text` | 该 run 最早的 `run.started` → 最早的**非空正文** `message.assistant_added`；每 run 一个样本 |
| `model_turn` | 每个 `turn.started` → 该轮首个正文（若无正文，取该轮终止事件）；**逐轮**样本 |
| `tool_call` | 按 `tool_call_id` 配对 `started` → `completed` |
| `settle` | 该 run 最后一个非终态事件 → 终态 |
| `runs` / `runs_without_start` | 样本数与**从未取得执行权**的 run 数（显式暴露，不静默丢弃） |

口径约束（与 `latency-summary.md` §4 一致）：

- 首控制事件、心跳、`turn.started`、工具事件、reasoning **一律不冒充**正文首字。
- 缺失即 `n=0` / `null`，不零填充。
- **不使用** `lease_expires_at` 倒推被租走时刻（run 的 lease 由心跳前移）。
- 无正文的 turn 不得把后续 turn 的正文算到自己头上（实现中按 turn 区间取首个正文，
  无正文则该轮终止事件收口）。

## 3. 生产真实数据（只读，隔离副本）

在服务器用 `.backup` 复制到 `/tmp` 隔离目录、`DATA_DIR` 显式指向该目录并断言路径不含
生产 db 目录，运行探针读取 `_phase_breakdown`。生产仅 2 个 assistant run：

```text
runs: 2   by_status: {'succeeded': 2}
queue_wait  {'n': 2, 'p50_ms': 940,    'p95_ms': 11051, 'max_ms': 11051}
first_text  {'n': 2, 'p50_ms': 4293,   'p95_ms': 53044, 'max_ms': 53044}
model_turn  {'n': 4, 'p50_ms': 33151,  'p95_ms': 53043, 'max_ms': 53043}
tool_call   {'n': 2, 'p50_ms': 0,      'p95_ms': 0,     'max_ms': 0}
settle      {'n': 2, 'p50_ms': 7,      'p95_ms': 10,    'max_ms': 10}
```

逐 run 分解（SQL 与 `_phase_breakdown` 交叉核对一致）：

| run | 总时长 | 入队→执行权 | 执行权→首正文 | 轮数 | 工具 |
| --- | --- | --- | --- | --- | --- |
| 1 | 103.74s | 11.05s | 53.04s | 2 | ~0ms |
| 2 | 38.40s | 0.94s | 4.29s | 2 | ~0ms |

## 4. 归因结论

- **模型生成是绝对主导**：`model_turn` 逐轮 p50 = 33.15s，两 run 的 4 个 turn 合计占各自总时长的绝大部分。
- **排队不是主导**：`queue_wait` p50 0.94s。
- **工具不是主导**：p50/max 均为 0ms（该样本为无实质耗时的只读工具）。
- **本地落库/结算不是主导**：`settle` max 10ms。

因此「助手响应很慢」在本样本中**主要是上游模型生成时间**，不是本地排队、工具或落库。
这与 `latency-summary.md` §6「待实测」中「worker 排队分布」一项相符：已实测，非主导。

### 样本限制（不得过度解读）

- **n=2**，且是两个早期 run（id 1/2）；不足以宣称稳定 p95，只能作为个体时长与范围证据。
- 未观测：真实推理档位、代理是否缓冲、SDK/代理重试次数、慢 chunk 是否真实发生、
  本地与服务器时钟偏差、压缩是否触发。
- 未做受控本地延迟注入实验（A-03 的另一半），故「上游流首正文 vs 最终 `message_end`」
  的差值仍属未实测。

## 5. 展示层：delta → 可见的差值已实测

`mapSessionEvent` 忽略 `message_update`/delta，只在 `message_end` 转发正文。该结论此前只是
**读代码得出**；现已用**真实 Pi SDK + fake stream**（无 egress，`fetch` 被阻断断言）把它变成
可执行证据：`agent/test/assistant-delta-gap.test.ts`。

测量结果（真实 SDK 事件流，3 个 `text_delta` 各间隔 40ms）：

| 量 | 观测 |
| --- | --- |
| 上游 `text_delta` 数量 | 3（确实到达，跨距 ≥ 80ms） |
| SDK 向订阅者转发 `message_update` | 是 |
| 公共事件 `message.assistant_added` | **恰好 1 次**（在 `message_end`） |
| 首个 delta → 首次可见 | ≥ 80ms（等于最后一轮 delta 的到达） |
| 发布正文 | 完整答案，不是部分前缀 |

即：**用户可见首字严格晚于上游首块正文，差值等于「剩余正文生成时间」**。这不是猜测，
是失败即红的断言（探针注入 delta 发布后该用例立刻失败，已还原）。

因此「首段显示晚」的成因已确认为**发布时机**（`message_end` 才可见），而不是 SSE 或渲染。
但**是否值得改**取决于用户对「首字更早但可能被终态替换」的取舍——属于产品决策，未实施。

## 6. 未做的产品选择（留给用户）

按 PRD A-R3/A-06 非目标，以下**未实施**，只提供证据与选项：

1. **逐字/增量显示**：`mapSessionEvent` 忽略 `message_update`/delta，用户可见首字 =
   `message_end` 时刻，而非上游首块正文。要改善「首段显示晚」需另立 delta 合同
   （重连/重复 delta/消息 ID/终态替换/部分失败）。**未改**。
2. **更快模型或更低推理档位**：会改变质量与成本，需用户决定。**未改**。
3. **并发/预算扩容**：本样本排队非主导，证据不支持此投入。**未改**。

## 6. 回归

`tests/test_admin_agent_latency.py`：

- `test_latency_metrics_decomposes_assistant_phases`：两轮 + 一次工具 + 无 `run.started` 的
  第二个 run，断言 `model_turn.n == 2`（不是把两轮合成一笔）、`first_text.n == 1`（每 run
  一个样本）、工具按 id 配对、`runs_without_start == 1`。
- `test_latency_metrics_phases_empty_without_events`：无样本时 `n=0` 且分位为 `null`。
- 既有 `test_latency_metrics_shape_and_percentiles` 的字段白名单同步新增 `assistant_phases`。

## 7. 与本任务无关的既有 flaky 测试（如实记录）

全量 pytest 在 `test_steward_candidate_evidence_integration.py::test_lease_expiring_while_actual_writeback_waits_for_sqlite_writer_is_not_adopted`
上偶发失败。已核实为**先于本任务存在**，不是本次改动引入：

- 在**未含本任务任何改动**的 `62a2b15`（B 之前）检出上跑全量，同样失败（一次 1642 passed / 1 failed）。
- 该用例单独运行连续 14 次全部通过。
- 该用例与 `admin_agent_latency` 无 import/调用关系（`grep` 零命中）。
- 该用例自身含真实墙钟 `time.sleep((deadline - utcnow()) + 0.03)`，全量运行时的
  CPU/IO 争用会使其越过 `batch.lease_until` 判定边界（250ms 预算）。

归因：既有计时敏感 flaky，非本任务回归。未在本任务中修改它（超出范围）。
