# 低延迟实测证据（2026-09-18）

只读核查，未调用模型、未改线上配置。

## 环境

- 远端 `familygraph-api.service` / `familygraph-agent.service` 均 active；agent `WorkingDirectory=/home/ubuntu/projects/FamilyGraph/agent`，`ExecStart=node dist/main.js`，`EnvironmentFile=/home/ubuntu/.config/familygraph/familygraph.env`。
- 远端代码 `8ae6414`，迁移 `0050`。**该库尚无 `timing_json` 与 `first_leased_at`**（0051/0052 未部署），因此 D 的精确源计时在当前线上不可用；本机 8000/8001 是 launchd SSH 隧道到该服务器。
- Provider：`liu-dada`，`openai_compatible` / `openai-responses`，`reasoning=1`，`context_window=272000`，`max_tokens=60000`，`thinking_levels=["low","medium","high","xhigh","max"]`。
- 空间设置：assistant 用 `gpt-5.6-sol`（空间 1、2）。
- 环境文件中未设置 `AGENT_LEASE_POLL_MS` / `AGENT_EVENT_FLUSH_MS` / `AGENT_PROVIDER_STREAM_MAX_RETRIES`，即走代码默认 2000ms / 250ms / 5。

## 两次真实 run 的分段（`agent_run_events.created_at`）

run 2（提问“你好，我是谁？”，`succeeded`，总 38.4s）：

| 阶段 | 时刻 | 时长 |
| --- | --- | --- |
| 用户消息入队 | 07:54:16.34 | — |
| run.started | 07:54:17.28 | 排队 **0.94s** |
| turn.started | 07:54:17.28 | — |
| 第 1 条 assistant（**text 为空**，仅工具调用） | 07:54:21.58 | 生成 **4.30s** |
| tool `familygraph.get_self_context` | 07:54:21.58 | 约 1ms |
| 第 2 轮 turn.started | 07:54:21.58 | — |
| 第 2 条 assistant（最终正文，34 字） | 07:54:54.73 | 生成 **33.15s** |
| run.settled | 07:54:54.74 | — |

run 1（`succeeded`，总 103.7s）：排队 **11.05s**，首条 assistant 生成 **53.04s**，第二轮生成 **39.63s**。

## 更正（同日晚，用 `agent_provider_egress` 审计对账后）

**上表把 33.15s / 53.04s 读成「模型单轮生成」，这是错的。** 出站审计按 run_id 对账后，同一轮的多次物理请求与其退避是可测的；上面的数字是「失败请求 + 退避 + 成功请求」之和。

`agent_provider_egress`（`target_id` = run_id，`created_at` = 该次尝试**完成**时刻）：

```
run 1: 05:08:00.581 failed 503 bytes=0     <- turn 1 的首次尝试，耗时 29.77s 才失败
       05:08:19.945 succeeded 200 91595
       05:08:53.767 failed 503 bytes=0     <- turn 2 的首次尝试，耗时 29.92s 才失败
       05:09:02.945 succeeded 200 88193
run 2: 07:54:21.138 succeeded 200 98229   <- turn 1（4.30s，一次成功）
       07:54:22.440 failed 502 bytes=0     <- turn 2 起连续 5 次快速 502
       07:54:24.098 failed 502
       07:54:25.718 failed 502
       07:54:28.413 failed 502
       07:54:32.660 failed 502
       07:54:54.719 succeeded 200 33727
```

逐轮分解（用 pi-ai 实际退避 `min(0.5·2^i,8)s×(1−0.25·rand)` 校验相邻间隔）：

| 轮次 | 失败请求 | 退避 | 成功请求 | 轮总时长 |
| --- | --- | --- | --- | --- |
| run 1 turn 1 | **29.77s**（1×503） | ~0.4s | ~19.4s | 53.04s |
| run 1 turn 2 | **29.92s**（1×503） | ~0.4s | ~9.2s | 39.63s |
| run 2 turn 1 | 无 | — | 4.30s | 4.30s |
| run 2 turn 2 | ~4.5s（5×502，本身很快） | **~11.6–15.5s（含末次退避）** | 14.1–16.1s | 33.15s |

相邻间隔 1.658/1.620/2.695/4.247s 只与**请求层**退避吻合（会话层退避是 2/4/8/16s，全部大于观测间隔，可直接排除）；即这两次 run 的等待都来自请求层重试，会话层未介入。

### 由此得到的三条修正结论

1. **主要可避免项是上游不稳定被重试放大；不能据此断言模型本身不慢。** 观测到的成功请求耗时为 3.9–19.0s，另有失败请求耗时 29.8s；run 2 turn 2 的请求层退避总量（含成功前最后一次）为约 11.6–15.5s。用户感知的 10～53s 由失败请求、退避、成功生成和消息公开缓冲共同组成。
2. **不存在「15s 请求超时」这条因果链。** `AGENT_REQUEST_TIMEOUT_MS=15s` 只作用于 sidecar→FastAPI 的 internal 调用（lease/context/events/tools）；模型流经 pi-ai 且未设 `timeoutMs`（SDK 默认 10 分钟），网关是 300s 总超时 / 10s 连接超时。所以 29.8s 的 503 是上游自己慢，当前没有首响应期限。先前把「15s 超时 + 2s 退避 + 16s」当作 33s 的候选解释，同样是错的。
3. **失败有两种形态，需要不同对策**：run 1 是「**慢失败**」（一次 503 拖 29.8s，退避无关紧要）；run 2 是「**快失败 + 用满退避预算**」（5×502 本身约 4.5s，整个请求层退避约 11.6–15.5s）。前者要补测并设计首响应/首内容期限，后者需要评估更小的交互重试预算。两者都是 E-R5 待批的策略变更。

### 仍然成立的结论

- **正文只在整条消息结束时公开**，所以这几十秒内用户看不到任何已生成文本；SDK 增量（`text_delta`）被 `agent/src/events.ts` 刻意丢弃。这与重试无关，是独立可改进项。
- **排队可达 11s**，run 2 为 0.94s（符合 2000ms 轮询期望值）；已在步骤 2 处理。
- 平台未显式设推理档位，SDK 回退 `medium`（`pi-coding-agent/dist/core/defaults.js:1`）。成功请求仍有 3.9–19.0s，是否需要 I 的档位 A/B，必须在 A+B 后用同配置数据判断，不能现在断言降档无关。
- `first_text_ms` 是 sidecar 收到首个**正文增量**的源计时，**不是网关收到响应头/首字节的时间**；不能直接拿它作为上游首响应 timeout 的证明。要做慢失败期限，E 还需补 gateway-side header/first-chunk timing 或使用明确的连接/读头 deadline。

## 结论

当前不能把 33–53s 简化成「模型生成慢」，也不能把 3.9–19.0s 的成功请求简化成「模型一定够快」。已确认的可避免等待是上游失败与请求层退避；未确认的部分包括首响应等待、首正文到达、推理耗时和公共事件缓冲。下一步采用 **A+B**：

1. **A（E）先做失败收敛的受控设计**：补齐 gateway-side 首响应/首 chunk 计时，基于真实 SDK + 假网关比较当前预算与交互预算；不直接把 `first_text_ms` 当 header timing，也不把快速失败当成功。预算变化必须记录请求数、退避、成功率/失败率与费用未知项。
2. **B（H）并行闭合安全增量协议**：先完成临时正文、最终正文替换、attempt/seq、重连去重、取消/失租、引用绑定和旧客户端兼容合同；安全准入通过前不发布裸 delta。
3. **I 的档位 A/B 后置**：如果 A+B 后真实成功样本的首正文或完整答案仍超目标，再对 medium/low 做经批准的有界质量对照；不在本轮擅自降档。

本文件此前末尾残留的「模型单轮 33–53s、无法区分生成与重试」旧结论已由本节取代。
