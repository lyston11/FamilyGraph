# 双 Agent 延迟：结论与可复用约束

基线 `main@62a2b15`。本文只收可复用结论；逐行证据见 `evidence/latency-baseline-2026-09-17.md`。

## 1. 两条链路不是同一种“慢”

- 助手：浏览器 → API 入队 → sidecar 自主 lease + heartbeat 续租 → context/SDK session → ProviderGateway → 上游流 → 多轮消息/工具 → 事件持久化 → SSE → 渲染。run 的 lease 由心跳前移，所以**不能**用 `lease_expires_at` 反推被租走时刻。
- 管家：领域触发/扫描 → core job（确定性 PFV 发布）→ detached delivery → assist batch（固定 lease，多笔串行模型调用）→ 审计/产物 → 写回栅栏 → 投影。
- 相同模型名不代表相同瓶颈。两者共享上游与 SQLite，但必须分别观测。

## 2. 管家已确认的执行缺陷（本轮主要修复目标）

1. `execute_batch` 逐笔发送但把结果留在内存 `results`，只在全部请求结束后统一审计保存；若后续请求耗尽租约，早先已返回结果也可能落不了库，恢复器只能记为 `unknown`（保守计费、不重发）。
2. `_apply_batch` 在业务栅栏之前就因同批存在 `unknown`/`failed` 直接终止；`recover_stuck_batches` 也用 `has_unknown` 优先标 `failed`。因此“只改成逐笔保存”不足以保证混合批次中的独立成功成果被消费。
3. terminology 的 `terminology_target_retryable` 以 viewer/root/target/semantic_hash 判断 durable 历史：`reserved`/`in_flight`/`unknown` 一律 `return False`，且该判断先于 `request_hash` 比较。所以新 job、新 prompt、新 request_hash 或新一轮 generation 都不解除同语义阻断。
4. `_post_json` 使用 `httpx.Client(timeout=...)`，属阶段/IO 等待限制，不等于整笔请求的墙钟上界；慢分块可能长时间占用执行线程。

边界不可削弱：网络只在有界执行线程与独立 Session；写事务内零外呼；失租旧执行者（owner/attempt/deadline 任一不符）拒绝审计与写回；unknown 保守计费且不自动重发。

## 3. 助手侧已确认的事件边界

`agent/src/events.ts` 的 `mapSessionEvent` 只在 `message_end` 转出非空 assistant 正文，`message_update`/delta 被忽略。因此“后端是流式”不代表“用户逐字可见”；首次正文可见时间与上游首块正文时间是两个量。

sidecar 相关默认值（部署 env 可能覆盖，须实测）：lease 轮询 2000ms、单次流请求最大重试 5、默认 lease 60000ms、事件 flush 250ms/批 20。单实例一次处理一个 run，排队阶段本身可能成为等待，但目前无证据说明它是主导。

历史结论边界（09-15 归档）：某次助手 run 33s 花在模型生成而非本地压缩；当时仅 2 turn / 6 条消息；`estimateTokens` 用 chars/4，中文会低估约 4 倍，但当时无生产证据且 overflow 自动恢复已兜底。这些是历史个例，不能当作当前全量慢响应的根因。

## 4. 观测口径约束

- 既有 admin 延迟接口对助手只有「入队→终态」总时长，无被租走/首事件分段，且明确记录应通过 FSM 转换点补生命周期事件而非改热路径。
- 管家 `latency_ms` 是逐笔真实耗时；`error_code=timeout` 为在超时预算处截断的删失样本，分位数解读必须结合 timeout 计数。
- 首控制事件、心跳、工具 turn、reasoning 都不得当作文档意义上的“首字”。
- 缺失即 unknown/null；不零填充、不用续期字段倒推、不用 generation 总行数代替推进判断。

## 5. 不可用做法

关闭 SDK 自动压缩 / recent-N 截断 / 再叠投影层摘要；为提速绕开工具授权、事实与写回栅栏、词表层级或云许可；用假流文本或模板文字制造“变快”；把历史 unknown 回填为 succeeded 或凭空解锁重试；用增大超时/租约冒充提速。

## 6. 待实测（未定论）

- 当前线上各阶段占比与真实推理档位、代理是否缓冲、SDK/代理重试次数、慢 chunk 是否真实发生、
  本地与服务器时钟偏差。以上均为假设，不得写成已定位的生产根因。

### 已实测（09-17 A，见 `evidence/assistant-phase-decomposition-2026-09-17.md`）

- **助手分段可测且无需新增字段**：`agent_run_events.created_at` + `run.started`/`turn.started`/
  `message.assistant_added`/`tool.execution.*`/`settled_at` 已足够拆解；原
  `admin_agent_latency` docstring 的「无法分段、应补 FSM 生命周期事件」说法与数据不符，已改写。
- **样本（n=2）**：`model_turn` 逐轮 p50 33.15s（4 个 turn）；`queue_wait` p50 0.94s；
  `tool_call` p50/max 0ms；`settle` max 10ms。**模型生成为主导，排队/工具/落库均非主导。**
- **delta → 可见的差值已实测**（`agent/test/assistant-delta-gap.test.ts`，真实 Pi SDK + fake stream，
  无 egress）：上游 3 个 `text_delta` 均到达、SDK 也转发 `message_update`，但公共事件
  `message.assistant_added` **恰好 1 次**且只在 `message_end`；首个 delta → 首次可见的差值
  等于剩余正文生成时间。**「首段显示晚」成因已确认为发布时机，不是 SSE/渲染。**
- **仍未实测**：真实推理档位、代理缓冲、重试次数、慢 chunk 是否真实发生、时钟偏差。
- **仍未实施的选项**（需用户决定）：逐字/增量显示（delta 合同）、更快模型或更低推理档位、
  并发/预算扩容。
