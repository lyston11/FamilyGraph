# A 设计：助手分段测量与证据驱动优化

## 1. 现有真源与边界

- sidecar：`agent/src/config.ts`、`worker.ts`、`session.ts`、`events.ts`；源码 SDK 版本以 lockfile 为准。
- egress：`backend/app/services/provider_proxy.py` 和 internal run 协议。不能绕过服务端模型/预算/执行身份校验直连上游。
- 持久事件与 SSE：`backend/app/services/agent_events.py`、相关 agent API；浏览器 `frontend/src/stores/agent.ts` 与 agent 组件。
- 现有读模型：`backend/app/api/admin_agent_latency.py`。先用它回答能回答的问题，缺失分段不得伪推。
- 历史依据：09-15 assistant-latency-optimizations、09-13 assistant-context-compaction、09-16 assistant-empty-final-answer；详见父 research summary。

## 2. 时序模型

| 记号 | 含义 | 建议测量所有者 |
|---|---|---|
| submit/accepted | 浏览器发出、API 确认入队 | 浏览器本地 duration + 服务端入队时间 |
| first_lease | 首次实际取得执行权 | 后端 lease 成功路径；不能由续租推算 |
| context_ready | context 取得/SDK session 就绪 | sidecar 相同 attempt 的 monotonic 区间 |
| upstream_start/first_byte/end | 每次真实上游请求 | ProviderGateway 安全元数据 |
| first_text/message_end | 正文首 delta 与完整消息 | SDK 事件，仅记时间，不记录文本 |
| tool_start/end | 每次工具调用 | 既有工具事件，关联 tool_call_id |
| event_commit/received/rendered | 公共消息到达用户 | 服务端持久事件与浏览器局部 duration |
| settle | run 最终状态 | 后端权威状态 |

字段名是概念，未批准新增协议；实现时只补确实缺失且必要的最少字段。具体时点名称必须说明属于字节、文本 delta、完整消息还是最终回答，不能用 `ttft` 隐藏歧义。

有历史重试/压缩时，每个 Provider 请求分别编号；SDK 重试、代理转发失败与工具后下一轮不能混称重试。需要时读取实际 SDK/adapter 文档与当前 CCSwitch/Pi 配置，但不擅自切换模型来源。

## 3. 测量优先顺序

1. 读取现有元数据、运行配置、进程/队列；证明请求是否等待 lease。
2. 本地真实 SDK + fake provider stream，控制 delta 到 message_end 的间隔，核对 `mapSessionEvent` 和前端显示。
3. 本地 HTTP 流服务经实际 ProviderGateway，检验 headers/chunks 是否及时向 sidecar 传递；不能只测一个 mocked adapter。
4. 工具/压缩/重试注入，用安全标记关联各阶段；保留历史处理与空回答合同。
5. 只有前述证据不足才追加最小生产可观测字段；与 schema/迁移兼容方案一起评审。
6. 经授权的隔离真实小样本，对照同配置 baseline，不对用户真实历史做额外外发。

## 4. 优化选择表

| 实测瓶颈 | 优先动作 | 不默认采用 |
|---|---|---|
| 等待 worker | 检查 lease auth、worker 可用性、已占用 run 和重试 | 凭空扩大并发 |
| Provider 首正文慢 | 核对请求参数、网关与上游每轮时间；提供模型/档位比较方案 | 私自降低质量/更换 Provider |
| delta 已产生、页面等 message_end | 提供增量显示的协议/UX 方案与预期展示收益 | 在 events.ts 直接透传所有 SDK 事件 |
| 工具调用慢 | 定位具体工具/权限读/串行必要性与安全缓存边界 | 缓存敏感权限结果或绕过确认 |
| context/压缩慢 | 区分历史恢复、RAG 注入与真实摘要请求 | recent-N、关自动压缩、投影层再摘要 |
| SSE/渲染晚 | 验证 flush、持久化、重连/去重及渲染时间；修正确边界 | 用假流文本掩盖延迟 |

当前只确定公共事件忽略 delta，不确定它是主要耗时。任何调优前先给该阶段 baseline 与预期可验证变化。

## 5. 若后续选择增量显示，必须补全的合同

这不是本轮实现承诺。须先评审：

- delta 只含允许公开的 assistant 正文，thinking/工具入参/返回和 Provider 私有字段不外泄；取消/失租即停止可见输出。
- message ID、顺序号、epoch、重连重放与去重定义；完成态权威正文替换临时内容，不拼接成重复文本。
- 不能每 token 一次 DB 写；聚合/频率限制、SSE 事件大小与持久化策略有界。
- 部分文本后失败明确标“未完成”；不污染正式历史与 RAG/记忆候选，不把工具前说明当最终成功。
- citations/cardIds/webCitations 只在权威事件确认后绑定；不能把未验证文本中的伪引用转可点击链接。
- 新旧 backend/sidecar/frontend 的滚动兼容、decoder 及 smoke 顺序明确。

在这些决定落文档并获批准前，维持现有 message_end 合同。

## 6. 隐私与成本

日志只留元数据。跨进程时间比较记录时钟精度，统计不要用零填充缺失。新增诊断不得制造模型请求或重复用户消息。真实调用限定合成输入、已授权 Provider、次数/token 上限；修改模型/档位/预算单列提案，不隐含在“诊断”中。

## 7. 回滚与完成

观测字段可兼容缺失；迁移若需要，先隔离演练，历史不虚构回填。程序优化按共同边界小步提交并有回归；若跨层协议变更，则成套发布/回退，不单独回滚一侧导致无法解码。

交付时必须列出：诊断已证明什么、改动减少了哪个阶段、真实上游仍耗时多久、样本量和误差、哪些用户场景尚未覆盖。仅完成观测不意味着慢响应消失，父任务验收不得据此自动完成。
