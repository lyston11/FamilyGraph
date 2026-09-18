# 低延迟响应设计草案

状态：in_progress（P0-1 已实现并部署；P0-2 增量显示已实现并通过全量回归，待部署与真实浏览器验收）。体验目标已由用户确认（3s 首段/8s 完整），重试降额仍待 E-R5 批准。增量显示合同由本任务承接（原 H 任务已归档，见文末「增量安全合同」）。

## 数据流与测量

浏览器提交 → API 入队 → sidecar 首次 lease → context/session → 网关上游请求 → SDK 首正文/完整消息 → 安全公开事件 → DB 提交 → SSE → 浏览器渲染。

复用 D 的 first_leased_at、源 timing、E 的出站审计和现有浏览器 performance，不新造 trace 平台。先区分首次调用成功、重试、工具轮次、压缩；首 headers/控制帧/thinking 不算首正文。当前生产源数据是否充足待只读验证；缺少首正文时先用隔离探针补测，不给历史回填。

## 建议推进顺序

1. 核对真实服务进程、已加载版本、迁移和配置来源。main 集成不等于线上加载；本机端口可能是 SSH 隧道。先只读安全元数据，不读/记录真实用户正文。
2. 冻结无重试正常请求基线，再区分 upstream 首正文慢还是 message_end 之前的展示缓冲慢。
3. 轮询优化采用最小方案：验证有后续任务时去除不必要的完成后 sleep；评估空闲轮询 2000→250ms 的延迟收益和租赁写事务压力（请求频率约增至八倍）。这是候选，不能先默认全局调小或增加并发。若负载不合适，再评估已有通知/有界长轮询，而非直接引 Redis。
4. E 的候选低延迟策略：仅请求层允许 1 次短重试、关闭会话普通网络自动重试；单次生成调用最多 2 次物理请求，而非一个含多个工具轮的 run 最多 2 次。保留自动压缩/overflow 恢复。对 Retry-After、已发送未知和流中失败先用锁定 SDK 验证，不把 audit.retryable 当作 SDK 已执行的控制。较长上游故障更容易失败，换取不隐式重启整轮；待 E-R5 批准。
5. 安全增量由本任务实施（承接已归档 H 的合同，见「增量安全合同」）：后台 schema 先定义 message identity/attempt/seq、临时与最终权威替换。**已核实：当前后端 append 路径与 sidecar `message_end` 均不对 assistant 正文做输出侧扫描**（`agent_events.py` / `agent_citations.py` / `internal_agent.py` 无 `contains_secret`/`contains_pii` 调用），因此增量与完整消息面对的是同一（缺失的）输出检查——不得声称「前缀安全检查等价」，只能按 H 的合同保留缓冲或按有界策略发布，并把这个事实作为采用门槛。跨片密钥/敏感模式不能靠最终撤回弥补。引用和 card 只在权威结果后绑定，临时文本不入历史/Memory/RAG。
6. 如果首 SDK 正文已经大于目标，以上展示改动不能消除这部分等待；由 I 提供同模型不同档位或更快 Provider 的有界 A/B 选择，不未经批准改模型。

## 时间与费用口径修正

E 的快速失败测试约 79s 是样本，不是硬上限。总等待包含各物理请求连接/推理/读取耗时、各层退避、Retry-After、工具、压缩、排队和显示；HTTPX 分阶段 timeout 不等于总墙钟 deadline。暂不以猜出的 10s/15s 作为生产上限。

关闭重试不会缩短一次成功推理。请求数量是费用风险代理，不能直接等于计费 token 或金额；失败是否计费、缓存折扣、实际 usage 均未知，不要求用户先提供价格才能做零费用受控研究。

## 兼容与安全

- 沿用既有 runtime、模型、完整历史、自动压缩、授权及引用合同。
- 所有增量要经后端注册/schema/读时授权；旧客户端仍收到完整消息，不丢终态；跨主体和 attempt 清临时状态。
- 取消/失租阻止新请求且有界关闭连接，不能只在下一块到达后检查；具体 deadline/预算在证据与选择后冻结，当前不宣称已实现。
- 无计划外 migration；如需要持久事件扩展，按「增量安全合同」的发布顺序（backend 先接受 → sidecar 再产生 → frontend 最后启用）。
- 回退按单个获批代码/配置执行，保留审计与用户历史，不能用整份 env 覆盖。

## 增量安全合同（承接原 H 任务，2026-09-18 归档）

H（`09-17-assistant-incremental-delivery`）为方案任务、未进入实现，其 PRD/design 的全部约束已由本任务吸收，原目录移入 `archive/2026-09/`。以下为实施时必须满足的合同：

**数据路径**：SDK text_delta → sidecar 有界聚合（`RunEventBuffer`）→ 后端 schema/权限校验 → 持久事件后广播 → frontend 按 run×turn 临时投影 → 权威 `message.assistant_added` 整体替换。

**实施结果与规划差异（2026-09-18，以实际交付为准）**：

- **未采用 `message_key`/`message_revision`/`delta_seq`/`text_fragment`/`final` 字段**：已核对 SDK（pi-ai 0.84.3）的 `AssistantMessageEvent` 不携带跨 delta 稳定的消息 id，`message_update` 只给 `contentIndex` 与 `partial`；而聚合在 `RunEventBuffer` 内完成，天然只有一个进行中的 turn，所以身份是 **(run, turn)** 而不是 message key。实际字段是闭合的两个：`assistant.text_delta = {role:"assistant", delta}`、`assistant.text_reset = {role:"assistant"}`。没有 `final` 标记——权威性由事件类型（`message.assistant_added`）区分，不靠 payload 布尔位。
- **attempt 变化用 `auto_retry_start` → `text_reset`，不用 `message_start`**：已核对 SDK，`auto_retry_start` 会丢弃失败的 assistant 消息并在**同一 turn 内**重生成（不重新发 `turn_start`）。因此“收到新 message_start 才清空”的草案判据在本 SDK 上**永远不会触发**；已 flush 的分片也无法靠后续少发收回，必须显式 reset。
- **有界性已冻结**（原计划等 F 基线后再定）：单帧上限 `MAX_PROSE_FRAGMENT_CHARS = 2000` 码点（按码点切分以免拆开代理对）；flush 边界是“任何非 `message_update` 事件”，因此聚合窗口由既有 250ms drain 节拍自然上界；后端再夹一次 `MAX_PROVISIONAL_DELTA_CHARS = 4000`，超限 422。理由：后端 16 KiB 载荷上限会拒绝**整批** append，所以超大 delta 必须拆分而不能整体发出。
- **输出安全采用合同允许的“明示未覆盖”分支**：已核实 append 路径与 `message_end` 均无输出侧扫描，本实现**不声称**前缀检查等价，也不把最终覆盖当作对已泄露片段的撤回；该事实已写入 `spec/backend/agent-runtime.md` §4 作为显式记录。若后续加入输出侧检查，增量分片必须与完整消息同门禁。
- **发布顺序在本仓库内是硬约束**：后端对未知事件类型返回 422（`_validate_entry`），因此**后端必须先于 sidecar 部署**，否则 sidecar 的新事件会让整批 append 失败、run 直接失败。旧前端不受影响（`stores/agent.ts` switch 的 default 分支忽略未知类型，已实测）。
- **回退**：前端先关（停止渲染临时气泡）→ sidecar 停产生（移除两处 emit）→ 后端最后移除注册。任一步都不需要迁移，也不删除任何用户历史（临时事件本就不物化 `AgentMessage`）。

**状态与权威规则**：

| 状态 | 展示 | 权威规则 |
| --- | --- | --- |
| queued / 尚无正文 | 既有排队提示 | 不用空 assistant 事件熄灭等待（对应 CON-372） |
| receiving | 一条临时正文，标仍在生成 | 只接受同 run/attempt 且连续的 seq；重复忽略 |
| complete | 整体替换为权威完整正文 | 不把最终正文再 append 一遍；绑定最终 citations |
| failed / cancelled | 保留已显示的安全部分并标终态 | 不当作完成答案或历史 |
| 撤权 / policy 拒绝 | 立即隐藏临时内容 | 安全拒绝优先于保留 |
| attempt 变化 / 账号空间切换 | 丢弃旧临时投影 | 不拼接不同执行或主体的片段 |
| 断线 / 刷新 | 从合法重放或权威快照恢复 | 不从 localStorage 恢复正文 |

**字段**（已冻结，实现见上）：`assistant.text_delta = {role:"assistant", delta}`；`assistant.text_reset = {role:"assistant"}`。两端 extra-forbid，额外字段 422。事件持久 seq 负责 SSE 续传；消息拼接是「按到达顺序追加到当前临时气泡」，不需要额外业务序号（同一 run 内 sidecar 已保证顺序）。

**输出安全**：临时文本不得绕过输出 policy guard。已核实当前 append 路径与 message_end 均无输出侧扫描，因此不得以「已有 guard」为由放行增量；必须按上述有界策略发布，并在实现中给出该结论的显式处理（保留缓冲或明示未覆盖）。thinking、工具参数/结果不作正文。

**有界性**：已冻结——单帧 2000 码点（sidecar）/ 4000 码点（后端硬夹），flush 边界为任何非 `message_update` 事件；背压时靠聚合天然合并，不丢权威终态（`drain()` 先 flush 再取队列）。

**发布顺序与回退**：backend 先接受新事件类型 → sidecar 再产生 → frontend 最后启用；旧客户端继续收完整 message 事件。回退停止产生临时事件、保留最终完整正文链，存量未完成投影安全收敛，不删除用户历史。

**兼容**：新增事件类型对旧客户端是 additive（`frontend/src/stores/agent.ts` 的 switch 已有 default 忽略分支），但不得依赖该宽容替代能力协商。

## 任务边界与发布

E 保持重试策略唯一来源（请求层预算改动待 E-R5 批准）；I 保持模型/档位/并发取舍唯一来源；H 已归档，增量协议与安全合同唯一来源为本任务；F 提供受控矩阵与浏览器验收、G 提供发布与真实验证，其证据可复用但不替代本任务体验目标。相交文件与数据库/端口串行；本任务 worktree 已在 `feat/09-18-assistant-low-latency`。真实合成调用与生产操作需单独明确样本、物理请求/token/时间/费用上限，不因本草案自动取得授权。
