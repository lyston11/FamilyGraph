# D2：助手压缩阶段归属与计时修正

## 状态与目标

planning；父任务 `09-17-dual-agent-latency-result-integrity`，主会话内联。F 受控验收（A3-1/A3-4）证明 `compaction_ms` 的合同前提在真实 SDK 下不成立，且压缩耗时被错误计入 `settle` 阶段。目标：把压缩归到它真正发生的阶段，并让该耗时可见且不被误读。

## 背景与证据

真实 SDK（pi-coding-agent 0.84.3）广播顺序（F 的 A3-4 实测，`main@802925f`）：

```
 44ms compaction_start:threshold   ← agent_start 之前
 49ms compaction_end
 50ms agent_start / turn_start
298ms message_end / turn_end / agent_end
299ms compaction_start:threshold   ← agent_end 之后
544ms compaction_end（245ms）
544ms agent_settled
```

两条事实：

1. **压缩不在 turn 内**。SDK 在 `prompt()` 发送前与 `_handlePostAgentRun()`（`agent_end` 之后、`agent_settled` 之前）调用 `_checkCompaction`，两者都在 `turn_start → turn_end` 窗口之外。因此当前窗口 `turn_start → message_end` 恒采不到，`compaction_ms` 真实路径恒空（A3-1 fail、A3-4 `in_turn=0`）。
2. **轮后压缩目前被记进 `settle`**。`settle` 阶段 = 最后一个非终态事件 → 终态；`compaction_*` 不落事件，所以那 245ms 被当成结算开销。这是既有的**错误归属**，不只是缺字段。

D 的合同原文「`compaction_ms` 是该轮内 SDK 压缩…是 `duration_ms` 的子成分（摘要请求发生在 turn 内）」是**事实性错误**；`model_turn`（`turn_start → message_end`）在真实路径下**本来就不含**摘要耗时。

## 需求

| ID | 要求 |
| --- | --- |
| D2-R1 | 删除「压缩是正文事件的子成分」这一错误合同；`compaction_ms` 不得继续挂在 `message.assistant_added` 上。 |
| D2-R2 | 轮后（`agent_end` 之后）压缩必须作为独立阶段可见，且不再计入 `settle`。 |
| D2-R3 | `model_turn` 语义不变：仍是该轮纯生成（`turn_start → message_end`），不含压缩；不得用「放宽窗口到整轮」的做法把等待模型的时间算成压缩。 |
| D2-R4 | 两侧同步：事件类型注册、payload 闭合、timing fail-closed 规则与前端类型表一致；不新增可由客户端伪造的字段。 |
| D2-R5 | 回归必须用**真实 SDK 广播顺序**锁定，不得继续用 SDK 不会产生的合成顺序（现有单测正是因此全绿）。 |

## 验收

| ID | 可观察结果 |
| --- | --- |
| D2-AC1 | F 的 A3-1 格通过：发生压缩的 run 有 `compaction` 样本，且与 `model_turn` 分开读数。 |
| D2-AC2 | F 的 A3-4 格改写为断言「压缩落在 turn 之外」并通过（把已证实的事实写成断言，而不是让它继续 fail）。 |
| D2-AC3 | 轮后压缩时间从 `settle` 中消失：`settle` 不再包含 `agent_end → agent_settled` 的压缩跨度。 |
| D2-AC4 | 真实 SDK 顺序的回归失败可复现：把压缩事件改回挂正文则测试转红。 |
| D2-AC5 | 门禁全绿：backend ruff/format/mypy/pytest、agent tsc/eslint/vitest、frontend lint/type-check/test/build。 |

## 依赖与非目标

依赖 F 的 A3 证据（已完成）与 D 的源计时机制（`timing_json`，迁移 0051 已落地）。串行：与 E（取消语义）不共享文件，但仍按一次一个分支集成。

不实施：不做压缩质量评估、不改 SDK 压缩阈值/保留量、不改 `first_text_ms`/`retry_*` 语义、不为压缩新增公开可读正文、不回填历史行的 `timing_json`。

## 已知取舍

压缩前的 prompt 级压缩（实测 5ms）落在 `prepare`（取得执行权 → `agent_start`）窗口内。它本就不大，且 `prepare` 的语义确实包含「把会话准备好再开始」，因此**不**从 `prepare` 中扣除、也不重复上报；该事实写进 spec，避免读者把 `prepare` 的偏大误判为 context/session 创建变慢。
