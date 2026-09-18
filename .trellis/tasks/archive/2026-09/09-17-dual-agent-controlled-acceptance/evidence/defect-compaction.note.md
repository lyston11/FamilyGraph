# D-F2 `compaction_ms` 在真实 SDK 路径下系统性为空

## 复现

```
python3 scripts/smoke/run_controlled_acceptance.py --scenario A3-compaction --no-reused-suites
```

脚本先用真实 API 沉淀 14 轮真实历史（每轮一个完整 run，全部 succeeded），
再把本轮的 provider 上下文窗口降到 40k（预留 16,384），使 SDK 自己的阈值判定成立。
**不自己截断历史**。

```
A3-1  fail  seed_turns=14 compaction_samples=0
A3-2  pass  run=succeeded assistant=1
A3-3  pass  summary_calls=1 context_messages=23
A3-4  fail  compaction_starts=2 in_turn=0
```

## 压缩确实发生了

SDK 自己发出的 compaction 事件：

```
['compaction_start:threshold', 'compaction_end', 'compaction_start:threshold', 'compaction_end']
```

摘要请求实发（provider 调用日志，按内容识别）：

```
{"call": 2, "user_turns": 15, "last_user": "where did I leave the amber box?", "is_summarization": false}
{"call": 3, "user_turns": 1,
 "last_user": "<conversation>\n[User]: seed 0 amberbox amberbox amberbox amb", "is_summarization": true}
```

`compaction_settings = {enabled: true, reserveTokens: 16384, keepRecentTokens: 20000}`，
`compact_decision` 的 `direct_should_compact=true`，都说明压缩前提成立。

## 但持久化的 `compaction_ms` 为空

```
persisted_timings = [
  {"type": "run.started",            "timing": {"duration_ms": 151}},
  {"type": "message.assistant_added","timing": {"duration_ms": 276, "first_text_ms": 154}}
]
```

没有任何 `message.assistant_added` 带 `compaction_ms`。

## 机理：真实 SDK 把压缩排在轮次边界之外

sidecar 观测到的真实广播顺序（毫秒为侧车单调时刻）：

```
compaction_start:threshold  269
compaction_end              286
agent_start                 289
turn_start                  289
message_start/message_end   289   （工具调用）
message_start               321
message_update              442   ← 首个正文
message_update              564
message_end                 564
turn_end                    565
agent_end                   565
compaction_start:threshold  565   ← 第二次压缩
compaction_end              811
agent_settled               811
```

`sdk_compaction_events` 的两处压缩都落在 `turn_start → message_end` 窗口**之外**。

而 `agent/src/events.ts` 的累积窗口是：

```ts
if (event.type === "turn_start") { this.turnCompactionMs = 0; ... }
if (event.type === "compaction_start") { this.compactionStartedAt = this.now(); }
if (event.type === "compaction_end" && this.compactionStartedAt !== null) {
  this.turnCompactionMs += elapsed;
}
...
} else if (item.type === "message.assistant_added") {
  timing = this.elapsedFrom(this.turnStartedAt);
  ...(this.turnCompactionMs > 0 ? { compaction_ms: ... } : {})
  this.turnCompactionMs = 0;
}
```

因此两处压缩都被排除在窗口外，`compaction_ms` 永远缺省。

## 为什么这是合同前提错误，而不是测试写歪

`09-17-assistant-timing-observability`（D）的合同注释写的是：

> `compaction_ms` 是该轮内 SDK 压缩（`compaction_start`→`compaction_end`）的累计时长，
> 是 `duration_ms` 的**子成分**（摘要请求发生在 turn 内）。

「摘要请求发生在 turn 内」在真实 SDK（pi-coding-agent 0.84.3）路径下**不成立**：
阈值压缩在 `agent_end` 之后、`agent_settled` 之前执行。后果是：
`model_turn`（`turn_start → message_end`）在发生压缩的轮次里**不含**摘要耗时，
而读者按合同会以为它含（因此可能把摘要耗时误读成推理耗时）。

## 覆盖漏洞

`agent/test/events.test.ts` 用**合成**顺序锁定聚合：

```ts
buffer.onSessionEvent({ type: "turn_start" });
buffer.onSessionEvent({ type: "compaction_start", reason: "threshold" });
buffer.onSessionEvent({ type: "compaction_end", ... });
buffer.onSessionEvent({ type: "message_end", message: {...} });
```

这段合成顺序恰好是 SDK **不会**产生的顺序，所以单元测试全绿而真实路径恒空。
缺少「真实 SDK 广播顺序」的回归。

## 建议

先决定压缩应归属哪个阶段，再改实现：

- 若压缩确实发生在轮外，`compaction_ms` 不应挂在 `message.assistant_added` 上；
  可以单列到 `run.started`/新增阶段，或把窗口放宽到 `agent_start → agent_settled`。
- **不能只把窗口放宽到整轮**：那样会把 `turn_start` 与真正等待模型之间的时间
  算进压缩，制造与 D 想要避免的同一类误读。
- 需要一条用真实 SDK 广播顺序的回归（`agent/test/events.test.ts` 之外，
  类似 `assistant-delta-gap.test.ts` 那样对真实 SDK 断言的测试）。
