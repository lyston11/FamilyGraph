# A 验收记录：助手响应延迟分段定位

任务：`09-17-assistant-latency-diagnosis`（父：`09-17-dual-agent-latency-result-integrity`）
基线：`main@45f636a`；提交：`076d631`（backend 分段）、`32b80f1`（agent delta 差值）
证据：父任务 `research/evidence/assistant-phase-decomposition-2026-09-17.md`

## 结论一句话

**测量已完成且可复核；样本指向上游模型生成为主导。**「延迟诊断已完成」成立，
**「慢响应已解决」不成立** —— 本任务未做任何提速改动。

## 逐条 AC

| AC | 状态 | 依据 |
|---|---|---|
| A-01 基线标出可测与缺失阶段 | **部分** | 可测：`queue_wait`/`first_text`/`model_turn`/`tool_call`/`settle`/`runs_without_start`。缺失（已标明）：真实推理档位、代理缓冲、重试次数、慢 chunk、时钟偏差。受控场景矩阵（无工具/只读工具/多轮/压缩/排队/失败重试/取消失租）**未做**。 |
| A-02 多轮不误算为单次推理 | **通过** | `model_turn` 逐轮采样，回归断言两轮 = `n=2`；`first_text` 每 run 一次；工具按 `tool_call_id` 配对；`turn.started`/工具事件不冒充正文首字。 |
| A-03 本地延迟注入区分首正文与 message_end | **通过** | `agent/test/assistant-delta-gap.test.ts`：真实 Pi SDK + fake stream、`fetch` 阻断断言、3 个 `text_delta` 间隔 40ms → 公共事件恰好 1 次且在 `message_end`；测量本身零模型请求。 |
| A-04 已证实程序问题有最小修复回归 | **部分** | 未发现需修复的**程序性**额外等待（排队/工具/落库均非主导），故无「提速」声明。已交付测量能力本身 + 双向 pin 的回归。 |
| A-05 运行时不退化 | **通过** | 未改 `events.ts` 语义（探针注入后已还原，`git diff` 为空）；agent 127 测试、backend 全量全绿；未触碰自动压缩/历史/空回答合同。 |
| A-06 前端反馈不退化 | **未验证** | 未改前端；未跑浏览器真实链路（无前端改动，故未触发）。 |
| A-07 安全字段与脱敏 | **通过** | 新增字段只含阶段名与毫秒/计数，不含正文/prompt/thinking/工具返回/密钥；`fetch` 阻断断言证明测量无网络。 |
| A-08 真实小样本 | **部分** | 真实只读样本 n=2（生产仅 2 个 run，已用尽）；未做真实模型压测对照（不重发私密历史）。 |

## 关键量化（生产只读，隔离副本）

| 阶段 | p50 | max | 判读 |
|---|---|---|---|
| `queue_wait` | 0.94s | 11.05s | 非主导 |
| `model_turn`（逐轮） | **33.15s** | 53.04s | **主导** |
| `tool_call` | 0ms | 0ms | 非主导 |
| `settle` | 7ms | 10ms | 非主导 |
| delta → 可见（受控） | = 剩余生成时间 | — | 发布时机所致 |

## 未做的选项（需用户显式决定）

1. **逐字/增量显示**：成因已确认（`message_end` 才发布），但合同未冻结（消息 ID/顺序号/epoch、
   重连重放去重、终态替换、聚合频率、取消即停、引用绑定、滚动兼容）。**未实施。**
2. **更快模型或更低推理档位**：改变质量与成本。**未实施。**
3. **并发/预算扩容**：样本显示排队非主导，证据不支持该投入。**未实施。**

## 与 B 的边界

- B 修的是**管家**批次的结果保全与总时限；本任务只做**助手**观测。
- 两者不共享租约语义，父验收不得互相替代。

## 未运行

- `frontend-api-smoke.sh`（未改 API 合同）。
- 浏览器端到端（未改前端）。
- 真实模型压测（样本已用尽，且不重发私密历史）。

## 既有 flaky（与本任务无关，如实记录）

全量 pytest 偶发 `test_steward_candidate_evidence_integration.py::test_lease_expiring_while_actual_writeback_waits_for_sqlite_writer_is_not_adopted`
失败。已在**未含本任务改动**的 `62a2b15` 检出上复现同样失败（单独运行连续 14 次全通过）；
该用例含真实墙钟 `time.sleep` 与 250ms 预算，全量运行时的 CPU/IO 争用会越过
`batch.lease_until` 判定边界。归因：既有计时敏感 flaky，**非本任务引入**，未在范围内修改。
