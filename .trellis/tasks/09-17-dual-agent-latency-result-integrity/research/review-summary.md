# 2026-09-17 复核：当前结论与任务路由

## 适用性与授权

源码审查基线 `main@d8d3668`。本summary取代 `latency-summary.md` 与 `integration-acceptance.md` 中关于严格截止、精确计时、部署已生效的过度结论；旧实验值保留为当时证据。用户已要求将全部问题创建最新子任务并写清PRD等，本轮仅完成规划。C～I全为planning，不启动实现、不部署、不真实调用、不使用子智能体。

原A/B已归档，历史冻结，不为新规则改写；已实现的逐笔持久化、混合批次独立恢复等仍有价值。父任务保持in_progress，新规划不得把全部修复重置成“未开始”，也不得因旧归档断言缺陷已闭合。

## 当前发现

| ID | 发现与证据边界 | 归属 |
| --- | --- | --- |
| Q01 | `_post_json`只在chunk返回后检查deadline，400ms预算实测638ms；阻塞headers/body未被总截止及时中断。 | C |
| Q02 | `execute_batch`未从剩余lease扣结算预留，事务/网络可吃完保存时间。 | C |
| Q03 | created_at是入库时刻；实际约125ms工具工作，同批append间隔1.301ms。run.started晚于context/session，queue_wait不是真实首次lease等待。 | D |
| Q04 | 单失败成功、失败耗尽统计漏算，零事件run漏分母；历史审计缺turn不能准确归属retry，不能只换一个计数公式。 | D |
| Q05 | 纯工具轮、压缩、源正文、SSE和渲染缺独立证据；不应把历史model_turn称纯生成，不能排除本地等待。 | D、F |
| Q06 | 上游所有>=400统一502可能误触发永久错误重试；部分连接异常缺egress审计。 | E |
| Q07 | 请求层retry与SDK默认session retry共同存在；总出站数需真实SDK假上游验证，不能把配置5误当全部预算。 | E、F |
| Q08 | 统一受控矩阵未完成；SDK假流不证明真实代理/SSE/页面行为，“未改前端”不能豁免浏览器。 | F |
| Q09 | 远端HEAD已d8d3668但API启动早于修复，线上OpenAPI无assistant_phases；Git同步不代表运行加载。 | G |
| Q10 | 两个历史run早于修复，不能作新版本效果；同配置真实合成样本/预算仍待批准。 | G |
| Q11 | delta等到message_end发布的边界已证实；增量方案须解决重放/安全/引用/取消/兼容，未选择上线。 | H |
| Q12 | medium降档、换模型、并发、轮询、预算取舍缺可靠收益和质量对照；默认保持当前配置。 | I |
| Q13 | 原AC04/08/09及Spec/HANDOFF过度结论需修正；AC06不强制降档/提速，允许有证据的上游瓶颈与方案交付。 | 父任务、C/D、F/G |

细节与再现见[复核证据](evidence/review-audit-2026-09-17.md)。本次规划没有重新运行模型或生产探针，远端版本与时钟值是审核时点快照，执行前须再查。

## 顺序与职责

C 总截止 → D 计时/聚合与关联合同 → E 错误分类/完整审计/重试 → F 受控全链矩阵 → G 部署与真实验证。共享backend/SDK/SQLite/端口时一律串行。H/I可先写方案，收益/采用判断依赖D/F/G证据，不阻塞现配置可靠性修复。D定义请求元数据，E生产审计，F验其一致；避免三处各造一套协议。

### 已闭合（本轮实现，待集成后复验）

| ID | 结果 | 证据 |
| --- | --- | --- |
| Q03 | 已闭合：新增 `agent_run_events.timing_json`（sidecar 源计时，迁移 0051）与不可变 `agent_runs.first_leased_at`；`queue_wait` 不再把 context/session 准备算进排队，短阶段不再被 250ms 批量 flush 量化成约 1ms | [D evidence](../../09-17-assistant-timing-observability/evidence.md) |
| Q04 | 已闭合：分母改由 run 表 LEFT JOIN（`runs_without_events`/`runs_without_first_lease`/`runs_without_start`）；单次失败后成功单列 `unmeasured_retries`，尾部失败耗尽不再丢失 | 同上 |
| Q05（部分） | 轮内压缩已作为 `model_turn` 子成分单列（`compaction`）；SSE/渲染仍需 F 的浏览器测量 | 同上 |
| Q06 | 已闭合（待集成复验）：网关按上游真实状态码分类，4xx（除 408/409/425/429）以原状态码 + `AGENT_PROVIDER_UPSTREAM_REJECTED` + `x-should-retry:false` 返回，不再折叠为可重试 502；连接异常/响应头失败/流中断/取消/成功各写恰好一条安全 `agent_provider_egress` 终态，含 `error_class`/`retryable`/`sent` | [E evidence](../../09-17-assistant-retry-governance/evidence.md) |
| Q07 | 已闭合工程部分（待集成复验）：真实 SDK + 本地假网关实测两层相乘，出厂配置最坏 24 次出站、整轮约 79s；`SESSION_RETRY_BUDGET` 显式冻结防 SDK 漂移。**降低总预算未批准**，策略表交 E-R5 独立决策 | 同上 |

仍未闭合：Q01/Q02 已在 C 完成（待集成复验）；Q08 属 F；Q09/Q10 属 G；Q11 属 H；Q12 属 I；E-R3/E-R5 的预算数值选择待用户批准。

C～G是必需闭合项；H/I是方案与决策交付，创建并不批准上线/调参。G真实小样本上限是提案（见G PRD），须单独确认费用等，不自动消耗。

## 验收解释

AC01部分（计时/分母/矩阵缺口）；AC02/03/05保留已有本地回归通过但F须累计复验，不代表线上生效；AC04未通过；AC06部分（归因证据仍不足，不因未提速本身判失败）；AC07未完成；AC08未完成（真实浏览器与发布环境未验证）；AC09未完成（子任务修复、文档与发布/清理未闭合）。

安全合同始终有效：unknown不自动重放、旧执行者零写回、网络不进写事务、云许可/预算/空间权限保持、SDK自动压缩保留、观测不存正文/密钥。旧spec中“created_at足够精确计时/无需任何字段”“chunk循环已实现整笔总截止”是待修正的事实性错误，不作为禁止修复的规则；实现子任务须同步修正相应Spec，不能以本summary弱化安全边界。
