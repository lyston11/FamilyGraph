# D2 技术设计

## 实测事实（决定设计）

真实 SDK（pi-coding-agent 0.84.3）在 `_runAgentPrompt` 内两次调用压缩，**都在 turn 之外**：

| 触发点 | SDK 位置 | 相对 turn | 现在落到哪 |
| --- | --- | --- | --- |
| prompt 前阈值压缩 | `prompt()` 内 `_checkCompaction` | `agent_start` **之前** | `prepare`（取得执行权→`agent_start`），实测 5ms |
| 轮后阈值压缩 | `_handlePostAgentRun()`（`agent_end` 后、`agent_settled` 前） | `turn_end` **之后** | `settle`（最后非终态事件→终态），实测 245ms |

`model_turn` = `turn_start → message_end`，两条都采不到 —— 这是**正确**的（该轮纯生成确实不含压缩），错的是把它当成子成分的合同。

## 归属决策

**压缩是 prompt/run 级阶段，不是 turn 子成分。**

- 轮后压缩属于「这一轮跑完后为下一次请求整理上下文」，既不属于刚结束的 turn（那轮的生成已结束），也不属于 `settle`（结算=终态落库成本，与摘要无关）。
- 因此新增一个 run 级阶段 `compaction`，由新的 sidecar 事件承载，与 `prepare`/`settle` 同级。
- prompt 前压缩（5ms）**不**从 `prepare` 里扣，也不重复上报：`prepare` 的语义是「取得执行权→SDK 开始」，把会话准备好本就含压缩；重复上报会让两处数字相加超出真实墙钟。该取舍写进 spec。

## 载体：新增 `run.compacted`（sidecar-timed）

选它而不是「把 timing 塞进 settle 请求」，因为后者要破坏既有不变式「后端自有事件不得携带 timing」（`EventIn.check_timing_type` fail-closed），且 `SettleRequest` 是协议面，改动面更大。

`run.compacted`：
- 公共事件表新增，`public_payload` 恒为 `{}`（与 `run.started` 同形）；**timing 仍是内部证据**，永不进 `public_payload`。
- 非终态：不进 `TERMINAL_EVENT_FOR`，不关 SSE 流。
- 在 `agent_settled` 处由 sidecar 发出，`timing.duration_ms` = 本次 prompt 内**所有** `compaction_start→compaction_end` 跨度之和（只计 `agent_start` 之后发生的，故天然排除 prompt 前那次）。
- 无压缩即不发事件（不写 0）。

`compaction_ms` 从 `EventTiming` 与 `EventTimingIn` 中**删除**（D2-R1）；真实路径下它从未有过值，历史行按 unknown 处理，不回填。

## 数据流

```
SDK agent_start ──► run.started        (timing: prepStartedAt→agent_start)   → prepare
SDK turn_start … message_end ──► message.assistant_added (timing: turn)      → model_turn / first_text / retry
SDK agent_end
SDK compaction_start/end ──┐
SDK agent_settled ─────────┴─► run.compacted (timing: Σcompaction)           → compaction（run 级）
worker settle ──► run.settled          (无 timing，后端自有)                  → settle
```

`settle` 自动修正：其定义是「最后一个非终态事件→终态」，`run.compacted` 现在是那个最后的非终态事件，因此 245ms 不再被算进结算开销。

## 聚合口径变更

`assistant_phases.compaction` 从「逐 turn 样本」变为「逐 run 样本」（`PhaseStats` 形状不变，`basis` 语义不变）。必须同步：

- `_timing_compaction_ms` 删除，改从 `run.compacted` 的 `_timing_ms` 取值。
- `model_turn` 不再与 `compaction` 成对读（不再有子成分关系）；文档改为「`model_turn` 是该轮纯生成，压缩单列为 run 级阶段」。
- `provider_retry` 的说明不变（仍是 `model_turn` 内的重试下界）。

## 风险与边界

- **新增公共事件类型是 additive**：前端 `AGENT_EVENT_TYPES` 是枚举表，store 的 `default` 分支忽略未知类型；但两侧类型表必须同步（D2-R4），否则类型检查与契约测试漂移。
- **不改** `first_text_ms`/`retry_*`/`queue_wait`/`prepare` 语义，不动 SDK 压缩阈值，不做压缩质量评估。
- 事件在 `agent_settled` 发出，早于 `flushAll`，因此与终态事件的 seq 顺序稳定。
- 失败 run 一般没有轮后压缩；没有压缩就不发事件，聚合 `n=0`，不零填充。
