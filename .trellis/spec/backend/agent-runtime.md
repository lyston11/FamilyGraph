# Agent Runtime 规范（V2.1 起）

> 权威来源：`app/schemas/agent.py`（internal 协议与浏览器 API 的请求/响应形状）、`app/services/agent_events.py`（事件类型注册表）、`app/services/agent_tokens.py`（token 合同）。本文是执行摘要；**两侧实现冲突时以后端 schema 为准**。

## 1. 拓扑与信任边界（不可违反）

- 浏览器只访问 FastAPI `/api/agent/*`；sidecar 只访问 `/internal/agent/*`；用户 JWT 打 internal 一律拒绝，service/run token 打浏览器面一律无效。
- `agent/` sidecar 禁止：DB 驱动、fs 写盘工具、shell 执行、任意 HTTP 抓取依赖；compose 中无端口发布、无 /data 卷。
- feature flag `AGENT_RUNTIME_ENABLED` 默认关闭；关闭时三处路由（browser/internal/admin provider）均 503 `AGENT_RUNTIME_DISABLED`/`AGENT_DISABLED`。

## 2. Internal 协议合同（六端点）

| 端点 | 认证 | 请求 | 响应 |
|---|---|---|---|
| POST /internal/agent/jobs/lease | service token | `{kind="assistant", leased_by, lease_ttl_seconds?}`；HTTP 端点只服务 Assistant sidecar；Steward 由 API maintenance canonical worker 直接调用确定性服务，不走该端点 | 200 平铺 `{job_id,run_id,agent_kind,attempt,tool_allowlist,policy_version,run_token}`；无可租 **204 空 body** |
| POST /internal/agent/jobs/{job_id}/heartbeat | run token | `{}` | `{ok:true, lease_expires_at, cancel_requested}` |
| GET /internal/agent/runs/{id}/context | run token | — | ContextOut（messages 为 `{id,role,content_json,created_at}`；provider.policy_result ∈ allowed/denied/denied_no_local/denied_cloud_forbidden；allowed 时 `base_url` 为**站内代理路径** `/internal/agent/runs/{id}/provider`，`api_key` 恒为 null——真实凭据/base_url 不出服务端） |
| POST /internal/agent/runs/{id}/provider/chat/completions 或 `/responses` | run token | 对应 Pi OpenAI adapter 的 JSON object body；空/非法/非 object 422 | ProviderGateway 代理（**唯一 egress**）：服务端 `resolve_runtime` 解密转发至已注册 Provider，成功流式透传 + `agent_provider_egress` 字节审计；上游错误一律 502 脱敏通用体，Run 非活跃或 `cancel_requested` 409，解析/解密失败 503 `AGENT_PROVIDER_PROXY_UNAVAILABLE`（fail-closed，绝不回退 sidecar env） |
| POST /internal/agent/runs/{id}/events/append | run token | `{events:[{seq,type,public_payload}]}` | `{accepted:[{seq,event_id}], duplicates:[int]}`（(run_id,seq) 幂等） |
| POST /internal/agent/runs/{id}/tools/{tool}/execute | run token | `{version,input,tool_call_id?}` | `{ok:true, tool, version, output}` |
| POST /internal/agent/runs/{id}/settle | run token | `{status:"succeeded"\|"failed", error_code?, error?}`（**不接受 cancelled**——取消由服务端裁决） | SettleOut |

请求模型全部 `extra="forbid"`：新增字段必须两侧同步。

## 3. Token 合同

- HS256 JWT，共享密钥 `AGENT_SERVICE_SECRET`；**typ 必须逐字一致**：service=`"agent_service"`、run=`"agent_run"`（两端各自实现过一次 typ 漂移导致 401，教训见 §6）。
- run token claims 绑定 run_id/job_id/agent_kind/account_id/space_id/tool_allowlist，exp ≤600s；校验失败 fail-closed + audit `agent_internal_authz_denied`。
- 错误码常量一律引用 `app/errors.py`，禁止字符串字面量绕过（check 发现项）。

## 4. 执行模型不变式

- run+job 同事务入队；本 runtime 只承载 assistant：每 session 一个 active run、每账户 ≤2 assistant 并发。Steward 是独立的确定性引擎，使用 `StewardJob`/maintenance 的每空间 active job 约束，不进入 agent runtime。
- 终态不可复活；lease 过期 reaper 收敛（回队重试→attempt 耗尽 expired；cancel_requested 直接 cancelled）。
- 事件先持久化再广播；(run_id, seq) 单调幂等；未知 type 拒绝不落公开流。新事件类型必须先在 `agent_events.EVENT_TYPES` 注册，sidecar 映射同步。
- **正文为空的 assistant 消息一律不产事件**（09-15 assistant-latency-optimizations 收紧判据，09-16 assistant-empty-final-answer 兑现延期项）：sidecar `mapSessionEvent` 对 `extractText(content)` 为空的 `message_end` 返回空，不产 `message.assistant_added`。理由是该消息没有可展示的正文：工具 turn 的调用本身由 `tool.execution.started/completed` 如实上报，而空最终回答没有任何内容。若照旧产出 `text=""` 事件，后端会持久化一条空 assistant 消息行（并在后续 run 作为空历史重放），前端「进行中」指示（`runActive && !messages.some(m => m.role === 'assistant' && m.text.length > 0)`）也会在工具 turn 阶段提前熄灭，最终还会渲染一个可见空气泡。过滤不占 seq 号（`RunEventBuffer.nextSeq` 按产出条目递增），后端不要求 sidecar seq 连续。
- **空最终回答结算 failed + `PROVIDER_EMPTY_ANSWER`**（09-16 assistant-empty-final-answer）：`worker` 跟踪循环中**最后一条**生成完成的 assistant 消息（`stopReason` 为 `stop` 或 `length`）的正文，为空或不存在时以 `PROVIDER_EMPTY_ANSWER` 结算 `failed`。判据**必须**包含 `length`：截断但有正文属「部分答案」，排除它会让这类 run 回归为误判失败。`toolUse`（不是答案，会被随后的空 `stop` 消息覆盖）、`error`（`PROVIDER_STREAM_ERROR` 优先）与 `aborted`（服务端裁决）不参与判定，判定插在 `leaseLost/cancelRequested`、policy guard、`PROVIDER_STREAM_ERROR` 之后、`succeeded` 之前。**本条与上一条必须同批发布**：只做事件过滤会把「可见空气泡」变成「静默成功」，用户连空气泡都看不到，比修复前更糟；任一单独 revert 需同时 revert 另一个。
- `PROVIDER_EMPTY_ANSWER` 是 **sidecar 运行期错误码**，与 `PROVIDER_STREAM_ERROR`/`SIDECAR_ERROR` 同类：只在 `frontend/src/api/agent.ts` 的 `AGENT_ERROR_COPY` 与本节登记，**不在 `backend/app/errors.py` 注册**（该表收录的是后端自己会发出的码）。`error_code` 列宽 64 字符足够，无迁移。
- **副作用工具红线**：服务端 (run_id, tool_call_id) 去重表 V2.4 才落地；在此之前禁止注册任何有副作用的工具（现有 echo/probe_scope 只读）。
- **取消门禁**：`cancel_requested` 是服务端权威状态。工具执行在 dispatch 前复核；ProviderGateway 在建立上游连接前及流式 chunk 边界复核，取消后拒绝/中断并记 failed egress audit。sidecar 的 AbortController/Pi `session.abort()` 只是加速路径，不能替代后端复核。

## 可观测性：助手耗时分段与源计时（09-17 A 建立，09-17 D 修正，09-18 延迟根因分析）

### 延迟根因分析（2026-09-18）

对 FamilyGraph agent 10-30s 响应与 Pi agent 1-2s 体感的对比调查确认：

**主因：流式可见性链路中断**
- `agent/src/events.ts` 的 `mapSessionEvent` **显式丢弃** Pi SDK 的 `message_update` / `text_delta` 事件
- 用户在整条消息生成期间（10-30s）只能看到空白等待
- Pi 的「1-2 秒」体感来自**首 token 或 thinking/正文开始滚动**，而非完整回答结束
- 对本地 Pi 约 33 个会话、1.2 万条消息的统计：同一 `liu-dada/gpt-5.6-sol` 模型的完整生成时长**中位数 = 11.47 秒**，低于 3 秒的比例仅 **0.8%**

**次级因素**：
1. **请求层重试放大上游不稳定**：`providerStreamMaxRetries=5`，实测尾部 11.6-15.5s 全是退避（Pi CLI 请求层 0 次重试）。09-17 E 已建立分层分类与 `retry-after-ms` 退避上限；预算数值仍待 E-R5 批准。
2. **单 worker 串行 + 2 秒空闲轮询**：实测排队 0.94s-11.05s。b067079 已改为 250ms + 去掉完成后睡眠，**已于 2026-09-18 部署到远端 backend 与 agent sidecar**（真实排队改善仍需样本）。
3. **prompt cache key 不稳定**（**已修复 2026-09-18**）：每 run 新建 `SessionManager.inMemory()` → sessionId 随机 → `prompt_cache_key` 每 run 变化 → cache miss（同上游同模型在 Pi 下有 72% 行 cacheRead>0）。已核实生产 `api=openai-responses`，该适配器在 `cacheRetention !== "none"` 时总是发 `prompt_cache_key`（`api.openai.com` 门控只属于 `openai-completions`）。现固定为 `fg-${account_id}-${session_id}`：跨 run 稳定、跨会话/跨账号隔离。**真实 TTFT 收益尚未实测**。
4. **per-chunk DB 事务**：每个 SSE chunk 都 `rollback + get(AgentRun)`。**2026-09-18 实测否决**：远端真实库上 median 368µs / p95 409µs，每 run 分片数 1–8 → 每 run 总开销 < 5ms，与早期「~200-500ms」估算差两个数量级。该复核同时是「流中取消/失租立即停止转发」的实现点，改为仅流首尾检查会破坏 `test_proxy_audits_cancellation_during_stream_once` 锁定的合同，故**不实施**。
5. **httpx client 每次 TLS 握手**：每请求新建 `AsyncClient`。**2026-09-18 实测**：真实上游上节省 ≈49.5ms/请求（59ms → 10ms，服务端各 6 次），远小于早期估算；属网关核心生命周期改动，**留档待另立**，本轮未实施。
6. **首响应头本身**：本批真实 `header_ms` 实测 1032–7844ms（无工具轮），是当前首段等待的主导项，属上游行为。

**已实施**：P0-2 增量显示（`assistant.text_delta`/`assistant.text_reset`，见 §4「增量显示合同」）——直接消除「正文早已到达却不可见」的 10-30s 空白；P1-1 稳定 cache key（见上，TTFT 收益仍未实测）；P0-1 轮询 250ms（已部署）。

**2026-09-18 验收结果**（n=17 真实短问答，`main@802925f`）：`first_text_ms` 中位数 1977ms、`duration_ms` 中位数 2618ms；「已生成但不可见」= `duration_ms − first_text_ms` 为 **151–491ms**（修复前等于整条生成时长）。完整答案 17/17 ≤ 8s；首段 16/17 ≤ 3s。**不得据此宣布「3s 必达」**：上游 `header_ms` 波动到 7.8s 时首段必然超标。证据：`.trellis/tasks/09-18-assistant-low-latency/research/acceptance-2026-09-18.md`、`browser-acceptance-2026-09-18.md`。

**优化策略**：详见 `.trellis/tasks/09-18-assistant-low-latency/research/pi-vs-familygraph-latency.md`

### 既有源计时合同

### 已修正的旧结论（不要回退）

- **`created_at` 是入库时刻，不是执行时刻**：sidecar 默认每 250ms 批量 flush 事件，
  同一批事件被集中写库。实测约 125ms 的工具执行经真实 `append_events` 后，相邻事件
  `created_at` 只差 1.301ms。旧 spec 声称“`created_at` 配合事件即可拆出精确阶段”
  是**事实性错误**；短阶段（工具、准备）必须用下面的源计时，不得用持久间隔冒充。
- **`run.started` 不是首次取得执行权的时刻**：sidecar 先 `getRunContext` + 创建
  Pi session，再由 SDK `agent_start` 触发 `run.started`。用它与入队时间相减得到的
  “排队”会把 context/session 准备时间算进排队。首次 lease 的权威时刻是
  `agent_runs.first_leased_at`（lease 时 attempt 0→1 写一次，不可变）。
- **不要用 `lease_expires_at` 倒推**被租走时刻：该字段被心跳持续前移。

### 源计时合同（`agent_run_events.timing_json`，迁移 0051）

- 新增 nullable 列 `timing_json`，形状由 `app/schemas/agent.EventTimingIn` 定义：
  `{source: "sidecar-v1", duration_ms, compaction_ms?}`。它是**内部证据**，
  永不进入 `public_payload`、永不透给家庭接口。
- `duration_ms` 是该事件处**结束**的那个阶段的 producer 单调时长：
  `run.started` = 取得执行权 → SDK `agent_start`（context 获取 + session 创建）；
  `message.assistant_added` = 该轮 `turn_start` → `message_end`；
  `tool.execution.completed` = 该次 `tool_execution_start` → `end`。
- `compaction_ms` 是该轮内 SDK 压缩（`compaction_start`→`compaction_end`）的累计时长，
  是 `duration_ms` 的**子成分**（摘要请求发生在 turn 内），schema 强制
  `compaction_ms ≤ duration_ms`。无压缩即缺省，**不写 0**。
- **两侧同步**：`timing` 只允许出现在 sidecar 执行事件上（后端自有事件携带即 422），
  未知 `source`、负值、越界、`compaction_ms` 用在非正文事件上一律 fail-closed。
  参与幂等指纹（`EventEntry.fingerprint`），重放同 seq 同 timing 视为重复。
- 历史行 `timing_json` 为 NULL：读取方按 unknown 处理，**不回填、不倒推**。

### 聚合口径（`GET /admin-api/v1/agent/latency` 的 `assistant_phases`）

- 每个 `PhaseStats` 带 `basis`（`source_clock` / `persisted_interval` / `mixed` / `none`）
  与 `native_n` / `derived_n`，**新旧样本不混成同一精度的分布**：有源计时的 run 用源计时，
  只有历史行的 run 退回持久间隔并明确标注 basis，不得把两者合成一个中位数。
- 分母是**全部符合窗口的 run**（LEFT JOIN 事件/审计），不是事件集合反推；
  零事件 run 计入 `runs_without_events`，无首次 lease 计入 `runs_without_first_lease`，
  无 `run.started` 计入 `runs_without_start`，都不静默丢弃。
- **口径红线**：首控制事件、心跳、`turn.started`、工具事件、reasoning 一律不冒充正文首字；
  `first_text` 每 run 一个样本（不是每轮），`model_turn` 逐轮一个样本（不得把多轮合成一笔）；
  无正文的 turn 不得把后续 turn 的正文算到自己头上。缺失即 `n=0`/`null`，不零填充。
- **`model_turn` 含上游重试与轮内压缩，必须与 `provider_retry`、`compaction` 成对读**
  （09-17 A/D）：pi-ai 在 5xx/408/409/429 上指数退避重试，重试发生在同一轮
  `turn.started`→正文之间，所以重试开销**已被计入 `model_turn`**。`provider_retry` 由
  `agent_provider_egress` 审计（**`target_id` 就是 run_id**，`detail_json` 含
  `status`/`upstream_status`/`bytes_read`，无 prompt/正文）推导：同一连续失败段内
  `末次失败 − 首次失败`。这是**下界**——审计只记完成时刻、不记请求开始，故段内首次
  失败自身耗时不可知；单次失败后即成功的段贡献 0 并单列 `unmeasured_retries`。
  `provider_retry.failed_attempts` 给出失败尝试总数（无歧义），`retry_segments` 是
  有可测窗口的段数，`exhausted_segments` 是失败耗尽的尾部段数。**不得**把 `provider_retry`
  当作全部重试耗时，也不得用 `model_turn − provider_retry` 宣称“纯推理时间”而不注明
  下界性质，更不得忽略 `compaction` 子成分。
  实测（n=2 run，只读副本）：run 2 的 5 次 502 在**同一轮内**连续，`provider_retry` 记录
  10.22s（该轮 `model_turn` 33.15s）；run 1 是**每轮各一次** 503，失败段长度为 1，
  按 `末次−首次` 定义得 0——即**单次失败的段不被测量**，只能由
  `provider_retry.failed_attempts=2` 看出有重试。这是本指标的已知盲区，不要用它的 n
  去反推「无重试」。
  触发源是上游 502/503 不稳定，不是退避上限（实测退避远小于
  `AGENT_PROVIDER_STREAM_MAX_RETRY_DELAY_MS=20000`）。
- **增量显示合同**（09-18 助手低延迟 P0-2 建立；承接已归档的 H 任务）：
  - **两个新事件类型**：`assistant.text_delta`（临时正文分片）与 `assistant.text_reset`（丢弃临时正文）。它们与 `message.assistant_added` 的区别是**权威性**：前者是显示投影，后者是结果。
  - **临时事件永不物化 `AgentMessage`**：`append_events` 只对 `message.assistant_added` 建历史行，因此临时分片不可能进历史、Memory 或 RAG 索引，也不带引用/权限声明（读时授权由 SSE/回放端点的账号归属复核承担）。这是「临时文本不入历史」的**机制**，不是约定。
  - **payload 闭合形状**（`_validate_provisional_payload`，fail-closed）：只允许 `{role:"assistant", delta}`（`text_reset` 不得带 `delta`）；额外字段、空 `delta`、超过 `MAX_PROVISIONAL_DELTA_CHARS`（4000 码点）都在落库前拒绝。
  - **sidecar 侧有界聚合在 `RunEventBuffer`**，不在 `mapSessionEvent`：纯映射函数保持无状态，`onSessionEvent` 累积 prose 并在**非 `message_update` 事件**处 flush，因此一个 token 不会写一行 DB，且分片相对工具/轮次事件的顺序正确（`message_update` 是唯一不 flush 的事件）。单帧按 `MAX_PROSE_FRAGMENT_CHARS`（2000 码点，按码点切分以免拆开代理对）拆分——后端 16 KiB 载荷上限会拒绝整批 append，所以超大 delta 必须拆分而不是整体发出。
  - **`assistant.text_reset` 是必需的，不是可选优化**：SDK 的 `auto_retry_start` 会丢弃失败的 assistant 消息并在**同一 turn 内**重新生成（不重新发 `turn_start`），而已经 flush 出去的分片无法被「后续少发」收回。因此 sidecar 在重试边界丢弃未 flush 的 prose 并发出 reset；前端收到后立即隐藏临时正文。
  - **前端投影**（`stores/agent.ts`）：`assistant.text_delta` 追加到分区末尾的临时气泡（`provisional: true`，无 id/引用），`assistant.text_reset` 或权威 `message.assistant_added` 到达时**整体移除**临时投影再合并权威消息（顺序不能反，否则去重会命中临时气泡）。临时气泡不参与 `replayCursor`，也不进 aria-live 播报（逐片重读整段会与权威播报重复）。
  - **输出安全（已核实的事实，不是假设）**：当前后端 append 路径与 sidecar `message_end` 都**没有**对 assistant 正文做输出侧扫描（`policy_guard` 只覆盖 input/tool_call/tool_result/context/before_provider_request 与 steward 出站）。因此增量分片与完整消息面对的是同一个（缺失的）输出检查：**不得**声称「前缀安全检查等价」，也不得把最终覆盖当作对已泄露片段的「撤回」。改变该结论前必须先有实际的输出侧检查。
  - **验证**：`agent/test/events.test.ts` 锁定聚合、thinking 不外泄、顺序、reset、拆分、跨轮不拼接；`agent/test/assistant-delta-gap.test.ts` 对真实 SDK 锁定「分片先于权威消息且拼接等于完整答案」；`backend/tests/test_agent_events.py` 锁定不物化历史与形状拒绝；`frontend/src/stores/__tests__/agent.spec.ts` 与 `AgentPrimitives.spec.ts` 锁定累积/替换/重置/终态保留/切换丢弃。
  - **不改变总生成时间**：本项只消除「正文早已到达却不可见」的等待（实测该等待为完整生成时长量级），推理耗时仍由 `first_text_ms`/`model_turn` 单独报告。
- **浏览器收到/渲染时刻不由本接口证明**：SSE 到达、首帧渲染需要浏览器 `performance` 时钟，
  服务端 UTC 与浏览器 monotonic 不可直接相减。**2026-09-18 已补测**（见任务 `09-18-assistant-low-latency`
  的 `research/browser-acceptance-2026-09-18.md`）：浏览器侧首段 2328–4689ms，且**探针不携带 run id，
  不可与服务端 run 配对**；另有已知盲区——SDK 分片与权威消息落在同一 250ms drain 窗口时，临时气泡
  可能从未以非空文本被绘制，探针随即观测不到（**未观测 ≠ 用户没看到**）。

## 5. Provider 治理（09-06 迁移后形态）

- **治理面在系统管理员域**：system_admin 经 `/admin-api/v1/agent/*`（admin_app :8002，ADMIN_JWT + `require_admin_ready`，router 级 runtime 503 门禁）管理 Provider 注册表（`POST/GET/PATCH /agent/providers`，secret 只写不读、secretbox 密文落库）、平台默认模型（`GET/PUT /agent/platform-defaults`，单行表 `agent_platform_defaults`，provider+model 成对）与空间设置只读排查视图（`GET /agent/spaces/{space_id}/provider-settings`）。写操作审计走 `admin_audit.record_access`（admin_access_audits，actor=system_admin；secret 永不入审计）。旧家庭挂载 `/api/admin/agent/*` 已删除（家庭 listener 一律 404）；`require_platform_operator` 仅余 controlled_web 等非治理路径。
- **选择权在空间 owner**：owner 经家庭域 `/api/spaces/{space_id}/model-settings`（GET 视图 / PUT 单维度 upsert / DELETE 恢复继承；权限=空间管理员）为 assistant/steward **分别**选择模型与云同意。空间设置按 `(space_id, agent_kind)` 唯一（0033 起双 Agent 维度）；`enabled=false` 行=显式停用（优先于平台默认，解析 `setting_disabled`）。
- **解析顺序（services/agent_provider.resolve_for_space，agent_kind 参数 fail-closed）**：空间显式行 → 平台默认回退（虚拟 setting，`cloud_allowed=False`/`local_required=False`——默认只决定通道档位，云同意仍归 owner；云默认在 owner 同意前 `denied_cloud_forbidden`）→ `POLICY_DENIED(no_space_setting)`。`platform_default_configured` 随 `PROVIDER_UNRESOLVED` detail 下发，供前端两态文案（通道未配置 vs 空间未选/未同意云）。
- 策略在消息创建时前置门禁：非 allowed → 409 可解释错误（PROVIDER_UNRESOLVED / PROVIDER_LOCAL_REQUIRED_UNAVAILABLE），**绝不静默换云**。
- P1 唯一 egress：sidecar 不持 api_key、不直连云端；模型请求经上表代理端点（run token 作 Bearer），`resolve_runtime` 为唯一解密出口。compose 中 agent 容器无外网（backend 网络 `internal:true`），外网 egress 仅 api 容器。sidecar 流重试经 `AGENT_PROVIDER_STREAM_MAX_RETRIES`/`_MAX_RETRY_DELAY_MS` 注入 pi-ai（5xx/408/409/429 指数退避）。

### 错误分类与分层重试（09-17 E 建立）

网关（`services/provider_proxy.py`）对**每一次真实出站尝试**写恰好一条 `agent_provider_egress` 审计，`detail` 在既有 `status`/`upstream_status`/`bytes_read` 之外带三个安全字段（机器码/布尔，无上游原文、无 prompt、无凭据）：`error_class`、`retryable`、`sent`。`sent` 是**发送确定性**：`false` 仅由连接未建立的证据得出（`ConnectError`/`ConnectTimeout`），`true` 表示上游可能已处理，不得用它声称“上游未处理”。

| 来源 | `error_class` | `retryable` | `sent` | 响应 |
| --- | --- | --- | --- | --- |
| 策略在发送前阻断 | `blocked_by_policy` | false | false | 409 POLICY_PROVIDER_BLOCKED |
| 上游 4xx（除 408/409/425/429） | `upstream_rejected` | false | true | 上游真实状态码 + `AGENT_PROVIDER_UPSTREAM_REJECTED` + `x-should-retry:false` |
| 上游 408/409/425/429/5xx | `upstream_transient` | true | true | 502 + `AGENT_PROVIDER_PROXY_UNAVAILABLE`（既有形状） |
| 连接未建立 | `transport_failure` / `transport_timeout` | true | false | 502 + `AGENT_PROVIDER_PROXY_UNAVAILABLE` |
| 建立连接/读头阶段的其他传输错误 | `transport_failure` / `transport_protocol_error` / `transport_timeout` | false | true | 同上 |
| 响应头之后流中断/超时/客户端断开 | `stream_interrupted` | false | true | 流已开始，连接被断开 |
| 流中复核发现取消/失租 | `run_cancelled` | false | true | 同上 |
| 正常结束 | （无） | （无） | （无） | `status=succeeded` |

- **永久上游拒绝不得伪装为可重试 5xx**：网关保留上游真实 4xx 状态，而不是统一转 502。实测（pi-ai/pi-coding-agent 0.84.3）`x-should-retry:false` 只约束**请求层**；若永久错误仍以 5xx 返回，Pi 的会话层仍会按错误文本重启整轮，真实出站数会翻倍。两处必须同批发布。
- **两层重试预算显式冻结**：请求层 = `AGENT_PROVIDER_STREAM_MAX_RETRIES`/`_MAX_RETRY_DELAY_MS`（可被 abort 中断，`Retry-After` 受 `maxRetryDelayMs` 约束；退避 `min(0.5·2^i, 8)s`×jitter）；会话层 = `agent/src/session.ts` 的 `SESSION_RETRY_BUDGET`（enabled/3 次/2s 起，退避 `base·2^(attempt-1)` 无 jitter），显式声明而不继承 SDK 默认值。真实出站数是两层相乘：暂时失败最多 `(requestRetries+1)×(sessionRetries+1)`（当前配置 6×4=24，实测整轮墙钟约 79s），永久 4xx 恰好 1 次。改预算属策略变更，需先有实际请求数/等待总量与可用性证据（E-R3/E-R5）；当前只冻结不降额。
- **压缩与重试分开**：context overflow 走自动压缩，不进任一层重试；空最终回答仍按 `PROVIDER_EMPTY_ANSWER` 结算 failed；取消/失租优先级不变（服务端权威）。
- `AGENT_PROVIDER_UPSTREAM_REJECTED` 注册在 `app/errors.py`（后端自己会发出的码）；sidecar 仍以 `PROVIDER_STREAM_ERROR` 结算，前端文案不变。
- 管理员延迟指标的 `provider_retry` 只计 `retryable != false` 的失败：`upstream_rejected`/`run_cancelled`/`stream_interrupted` 不是重试，计入会造出虚假重试段；历史审计行无该字段时沿用旧的 `failed` 口径，不回填。
- Provider profile 首版固定为 `liu-dada/gpt-5.6-sol`（`openai-responses`、272000/60000、reasoning、text+image、low/medium/high/xhigh/max）；代码门禁拒绝其他云 profile，且不提供可由 Compose 环境变量关闭的绕过开关；local Provider 仍可作为本地敏感数据回退。

## 6. Wrong vs Correct：双侧独立实现合同

### Wrong（V2.1 实际发生三次）
后端与 sidecar 各自按模糊描述实现，各自 mock 自测通过，compose 联调才暴露：
1. lease 请求 `{sidecar_id}` vs `{kind, leased_by}`；响应嵌套 `{job:{...}}` vs 平铺 LeaseOut。
2. token typ `"fg-agent-service"` vs `"agent_service"` → 全部 401。
3. settle body `error{code,message}` vs `error_code + error{...}`。

### Correct
1. 改协议先改 `app/schemas/agent.py`，sidecar 类型从 schema 抄写并在集成测试断言真实形状。
2. 共享字面量（typ、事件 type、policy_result 枚举）在一侧定义常量，另一侧测试逐字断言。
3. **每个涉及 internal 协议的任务，验收必须包含 compose 真实联调**（mock 不能证明合同）；本任务 E2E 链路：bootstrap→space→session→message(Idempotency-Key)→sidecar lease→context→settle→SSE 重放/Last-Event-ID 续传→幂等重放同 Run。

## 7. 测试要求（新增 agent 功能时）

- 并发约束冲突路径、终态不可复活、reaper 三分支收敛各有用例。
- token 篡改/过期/type 错用 → 401 + audit 行存在断言。
- SSE 断点续传无漏序、终态后连接关闭。
- Provider 矩阵五态 + secret 不回显。
- 重试治理：上游永久 4xx 恰好一次出站且响应体脱敏、暂时错误维持可重试形状、连接异常/流中断/取消各留恰好一条安全审计；两层重试用真实 SDK + 本地假网关对账实际请求数（`agent/test/retry-governance.test.ts`），并以 `SESSION_RETRY_BUDGET` 对照 SDK 默认值防止静默漂移。
- sidecar 侧：mock FastAPI 强制权威形状（严格校验请求体、204 空 body、ContextOut 归一化）。

## 8. 只读领域工具层（V2.2 起）

- 六个 `familygraph.*@1` 查询工具实现在 `services/agent_query.py`，注册表 schema 在 `agent_tools.py`：input 一律 `additionalProperties:false`（拒绝任意 actor/space 字段），scope 只取 run token claims；注册表校验器递归执行 string/integer 的 min/max、enum，以及 array/items 边界。
- **投影口径**：`get_profile_summary` 复用 `visibility.evaluate(purpose=PURPOSE_AGENT) + payload_from_decision`，与 users 路由同一对原语；purpose=agent 上限 lineage_summary ≤ profile API，只紧不松。禁止在工具内重写可见性规则或返回 ORM 对象。
- **防枚举**：不存在与不可见同码 `FG_PROFILE_NOT_AVAILABLE`；关系路径 BFS 对不可见途经节点剪枝，不泄露存在性。
- **零写入**：查询工具纯 SELECT；新增写类工具前必须先落地 (run_id, tool_call_id) 去重表（见 §4 红线）。
- **双侧 schema 同步**：TypeBox 声明与后端 input_schema 必须逐字段同类型同名——实际发生过 cursor string vs integer 漂移；新增字段时两侧同改并加快照测试。
- **Provider wire tool names**：后端 registry、run token allowlist、内部执行路径和公开审计事件始终使用规范 `familygraph.*` 名称；由于部分 OpenAI-compatible relay 拒绝函数名中的 `.`, sidecar 仅在 Pi/pi-ai 的 provider 出站声明中将其映射为 `familygraph_*`（例如 `familygraph.list_visible_people` → `familygraph_list_visible_people`）。收到 Pi 的 tool hook/event 后必须反向映射回规范名再做 allowlist 校验、执行和事件投影；未知名称 fail-closed。该 wire 适配不得改变后端 schema 或审计合同。
- **前端 SSE**：EventSource 无法带 Authorization header，必须 fetch+ReadableStream 手工分帧解析；Last-Event-ID 续传、401→refresh→重连一次收口、终态事件后不再重连。错误码→文案映射表须覆盖后端可能返回的全部 agent 错误码（含 run.failed 载荷中的 POLICY_*/PROVIDER_*/SIDECAR_ERROR）。

## 9. Sidecar ContextOut 解码门禁（V2.7 修复）

ContextOut 是后端到 Pi session 的跨层安全边界，sidecar 不得用默认值“修复”畸形投影。
`InternalClient.getRunContext()` 在构造 session 前必须拒绝以下情况，并返回
`invalid_context_projection`：

- `run_id/session_id/account_id/space_id` 不是正整数；`agent_kind` 不是
  `assistant`；`status` 不在后端 Run 状态枚举中；
- `attempt`、`next_event_seq` 不是非负整数，`policy_version` 为空或非字符串，
  `cancel_requested` 不是布尔值；
- `messages` 不是数组，或任一消息缺少整数 `id`、字符串 `role/created_at`、对象
  `content_json`；`tool_allowlist` 不是字符串数组；
- `context_blocks` 不是数组，或任一 block 缺少字符串
  `source_id/source_type/scope/sensitivity/citation/content`、非负整数
  `revision`。

Provider projection 的协议字段缺失/未知时必须保持未定义并由
`buildRunSession()` fail-closed；不得把 Responses profile 静默降级为
Completions。原始投影不写入日志或事件，错误只保留机器码和脱敏上下文。
Allowed runtime snapshot 还必须包含非空 `provider_revision`（由 Provider
`updated_at` 固化）；缺失或非法 revision 一律 `runtime_snapshot_invalid`，只对
明确标记的迁移前 legacy snapshot 走兼容分支。

### Wrong vs Correct

```text
Wrong: String(raw.run_id ?? "") / Boolean(raw.cancel_requested)
       → 后端契约破坏被伪装成可运行的 session。
Correct: 严格校验后才归一化；缺失或类型错误 → 502 invalid_context_projection。
```

### Required regression

至少覆盖一条缺失核心字段和一条 malformed context block；集成测试还要断言
畸形 projection 不会触发 Provider 请求或工具 dispatch。

Heartbeat 返回 401/403/409/410 时，sidecar 必须把 lease 视为已失效，立即 abort
Pi session 并跳过 settle；不能只把 410 当作 lease loss，否则 membership revoke
或服务端终态竞态会留下继续运行的模型流。
