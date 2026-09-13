# Design — 个人称谓呈现与通知

## 1. 复用点和边界

新增 `services/kinship_presentation.py`（最终名称可按目录习惯调整），由 PFV 当前投影、relationship_resolver 的 PathStep/概念码及 Terms 提供语义。它是展示组合器，不加载模型、不另算关系、不写事实。

`_serialize`、notifications 的 suggestion 分支、PFV 推测详情使用相同服务。关系档案 compose_resolution_view 与 PFV 使用同一有效称谓解析入口；避免一个读默认词、另一个读自动词。个人摘要与 topology 端点仍分开。

## 2. 响应合同

`KinshipPresentation` 采用严格 schema，概念字段如下：

| 字段 | 含义 |
| --- | --- |
| version | 1；新增字段兼容扩展 |
| availability | ready / refreshing / unavailable |
| reference_user_id、target_user_id | 本条称谓的参考人与目标；永不靠字符串猜方向 |
| term、term_source_level、origin | 当前有效叫法及来源；无有效称谓为 null |
| subject_display、object_display | 已按当前 viewer 可见性处理的人物展示值 |
| summary | 服务端生成自然方向句；候选必须带“可能”等状态 |
| relation_state | confirmed / inferred / proposal，不由 term 推出 |
| source_state、recipient_state | 同来源的共享状态与本人通知状态分开；不能把个人忽略写成全局驳回 |
| evidence.kind | confirmed_path / inferred_path / unverified_candidate / unavailable |
| evidence.related_fact_count | 仅可核验相关且可见的 confirmed 步计数；旧候选未知为 null |
| requires_action | 是否有当前用户需要处理的领域动作；称谓建议为 false |

输出中可保留受限的旧 value 字段兼容命令/旧客户端，但新 UI 不从 fact_type 构造展示。统一 TermSourceLevel 含 derived/steward，来源字样由闭合映射显示；未知来源采用“管家称谓/称谓”中性降级，不直接显示内部枚举。

示例：查看者是候选子女，subject 为父亲候选且性别已获准：`张先生可能是你的父亲`；若本人设置 Um=老爸，使用相应词。查看者与两端都不同则输出 `张先生可能是小林的父亲`，另以当前 PFV 可用结果标注各人与查看者的关系。不得写无向的 `张先生 · 小林：生物学亲子`。这些名字仅为合成示例。

候选尚未形成个人已确认路径时，不能把“你的父亲”作为无条件人物标签；只在带可能性的完整句中使用。现有已确认 PFV 则可以显示“你的爸爸”。

## 3. 数据读取和安全

先验证当前 account 对 space 的成员资格和目标可见性，再取当前 PFV。所有端点字段经 visibility payload，不能取 ORM name 直接输出。查看者关系路径需逐跳确认事实当前有效且节点可见；不可仅复核两端。

PFV 过期时复用现有安全空态/独立后台登记机制，不在通知 GET 物化视图。候选一步只用已授权端点和该候选的规范化 PathStep、Terms 来生成可能性描述。无法提供可信个人称谓时返回 availability 与自然、准确的关系线索空态。

如果 resolver 已有确认路径，只在路径语义能够证明该候选完全冗余时消除重复建议；不能凭同一显示文字就判定原子事实已存在。不得为了减少通知而自动确认未知候选。

## 4. 状态、旧详情与提交结果

引入同一 `effective_suggestion_state` 读模型，供 list/detail/notifications/allowed_actions 使用；它同时看共享状态、recipient.dismissed_at、expires_at、当前授权/证据、关联提案状态。命令仍在事务内重验。

| 条件 | 显示/动作 |
| --- | --- |
| 活跃关系线索且本人有处理资格 | 待核实；只给资格允许动作 |
| 本人忽略 | 已忽略，无 pending/submit/dismiss 重复入口 |
| submitted | 显示关联提案实际状态；不再次 submit，不宣称 confirmed |
| resolved / expired / superseded | 相应终态，无处理按钮 |
| 过期证据或授权失效 | 安全不可用/隐藏，不显示旧依据或名字 |
| term_preference | 可选偏好，无需处理；B 决定有效建议动作 |

新增 GET `/api/steward-suggestions/{id}?space_id=...`，与列表完全同授权/状态/序列化。前端点击通知按 ID 获取；列表照旧 keyset 分页，不把首页缓存当成完整仓库。store 缓存按 account+space 隔离，切账号或空间清理选中项和旧详情。

submit 的 linked_proposal/pending_confirmations 保存在 store 并显示服务端提供的状态；不把名单长度解读为全部人员都必须同意。保留现有真实确认入口范围，本期不增加新的确认按钮。重复请求仍保持 Idempotency-Key 语义。

忽略、失效和过期的有效状态在读时即可生效，不依赖用户再打开详情或一个尚不存在的过期 sweep。变更不重写历史行，不复活终态。

## 5. 推测层与拓扑交接

通过 source_candidate_id 等已有来源关联，让相同线索的 Suggestion 和 StewardInferredEdge 读到一致状态。保留 inferred 不进入 SourceFact 的边界；已有 confirmed topology 不消费个人摘要当连线。

“一致”指共享事实/提案状态一致，个人忽略仍只属于通知收件人：

| 操作/状态 | 本人通知 | 其他收件人 | 推测树 |
| --- | --- | --- | --- |
| 甲忽略 Suggestion | dismissed，无待办 | 保留各自原状态 | 不改全局 inferred，甲的树也不因忽略通知删线；按钮写“忽略通知” |
| 乙通过已有 inferred 入口全局驳回 | 共享 source_state=rejected，无 submit；已有个人忽略保持 | 同共享状态，无待办 | 按已有全局 rejected 行为不画该线 |
| 任一入口创建 proposed SourceFact | source_state=submitted，返回相同关联 ID；已忽略不复活 | 同关联状态 | 仍是推测，可显示提案进度，不造第二条提案 |
| 已有合法确认流程形成 confirmed fact | source_state=confirmed/done；不重新通知已忽略用户 | 按授权显示已确认 | 原推测停止，confirmed 结构继续由事实层提供 |
| inferred superseded/证据失效 | revoked/不可用，无 submit | 相同共享结果 | 不使用失效推测 |
| 显式恢复全局 inferred | 共享回 proposed，但本人 dismissed 仍保留 | 其他人按既有状态，不新增提醒 | 按已有恢复规则显示，不据此制造通知 |

授权复核先于两种状态；requires_action 由有效共享状态、当前资格及本人未忽略共同决定。个人 dismissed 不伪装成关系被驳回，global_state 不反过来清除 recipient 历史。

关联复用在两条现有入口的短事务内完成：共享 current-space + 规范化有方向三元组 + agent_proposal 的查找 helper；不得沿用未限定空间的查询。Suggestion 继续用已有 linked_fact_id 持久关联。inferred 入口产生/复用提案时同步同 source_candidate 的已有 Suggestion；Suggestion 入口先找同源/同空间已有提案再创建。较晚生成的 Suggestion 在投影时关联已存在的合法提案/确认事实。confirmed 结果始终以当前 SourceFact 验证，不把 inferred 标志单独当事实。无需增加确认入口或改资格，仅补来源关联与重复提交收敛。

InferredEdgePanel 使用统一 summary/term/evidence；修正原 `A — term — B` 歧义与确定性推断文案。改动其 PathStep 时特别核验单跳 from/to、方向和词所指对象；不重做路径搜索和布局。

## 6. 兼容与测试

无需数据迁移，新增响应字段和详情路由；历史通知运行时使用新投影。旧后端没有 presentation 时新 UI 显示中性描述，禁止恢复 raw enum 映射。

回归重点是 job/PFV→API→UI 的多 viewer 方向、derived API、遮罩字段、旧第 21 条详情、有效状态、同来源推测状态及 cache 清理。UI mock 只补交互，不能代替后端授权和 API schema 验证。

增加双用户/双入口序列：甲忽略通知→乙仍可看线；乙全局驳回→所有通知无 submit；先从任一入口提交再从另一入口提交→同一 SourceFact ID；相同人物在另一空间的提案不得被复用；显式恢复全局线索不复活个人通知。
