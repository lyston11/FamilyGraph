# FamilyGraph v2 Agent 系统规划

> 状态：Planning。本文是 v2 Agent 波次的产品需求权威来源；实现前还必须审阅各子任务的 `design.md` 与 `implement.md`。本轮不得运行 `task.py start`。

## Goal

在已完成的 FamilyGraph v1 上新增一套以 Pi Agent 为底层运行时、以 FastAPI 领域服务为唯一业务真源的双 Agent 系统：Assistant 面向当前用户完成家谱问答与关系解释，Steward 按单一空间维护派生关系、称谓、冲突和推荐卡片；任何正式家谱事实与成员变更仍由用户确认并由后端重新鉴权。

用户价值：每个人都能从自己的空间与称谓习惯理解亲属网络，同时不会因为 Agent、RAG、跨空间关系或管理员身份造成信息串线、静默写入或隐私扩大。

## Confirmed Facts

- v1 已实现完成，技术栈为 FastAPI + SQLAlchemy + SQLite/WAL、Vue 3 + Pinia、nginx + Docker Compose。
- 当前尚未部署，没有真实成员、账号或业务数据；v2 不需要生产数据迁移、双写或旧客户端兼容期，但空库迁移链必须可复现。
- v1 的 `visibility.py`、关系/空间 FSM、审计、认证和前端空间状态可复用，但现有 API 路由中的组合事务需抽为领域命令后才能安全暴露给 Agent。
- Pi 是 Agent SDK/harness，不提供 FamilyGraph 的授权、持久化、RAG 或业务状态机；这些能力由本项目补齐。

## Requirements

### R1. 两种产品 Agent 与窄工作流

- Assistant 以 `account_id + session_id + space_id` 运行，面向当前用户提供通用聊天、家谱问答、关系路径解释、受控 RAG；联网能力单独分期且默认关闭。
- Steward 以 `space_id + job_id` 运行，只维护当前空间的 DerivedFact、称谓建议、冲突和 ActionCard，不代表平台管理员，也不读取私人会话。
- 允许 ProfileIntakeExtractor、MemoryCandidateExtractor、KinshipResolver、ContextBuilder、RAGProjector 等内部窄工作流；它们不是额外产品人格。
- 确定性关系计算、授权与状态机不得交给 LLM；Pi 只负责自然语言理解、歧义处理、编排与解释。

### R2. 身份、角色与空间

- 认证主体、人物档案与确档状态分离；状态至少包括 Account `managed → claimed`、Profile `provisional → identity_confirmed`、Fact `proposed → confirmed | disputed`。
- 平台与空间角色分离：`platform_operator`、`space_owner`、`space_admin`、`member`；平台运营者默认没有家庭数据读取权。
- 平台运营者可签发短期、单次使用的 owner 邀请；被邀请者登录后成为新建 LineageSpace 的 `space_owner`，不会因此成为平台运营者或获得其他空间权限。
- 空间分为 HouseholdSpace 与 LineageSpace；PersonalFamilyView 是按当前查看者生成的派生投影，不是物理空间。
- 共享人物身份、分区事实、显式桥边；空间永不隐式合并。配偶可共同创建 HouseholdSpace，各自 LineageSpace 保持独立。
- owner 删除/注销前必须完成空间移交或显式终止流程，不再沿用 v1 的 owner FK 直接级联删除空间。

### R3. 正式事实、推断与称谓

- 数据分为 SourceFact、DerivedFact、Hypothesis/Recommendation。Agent 只能自动更新带证据版本的 DerivedFact 缓存，不能自动新增或改变 SourceFact。
- 稳定 SourceFact 内核包括亲生/收养/继亲父母子女、监护、配偶、伴侣、直接兄弟姐妹断言；朋友/同事属于 SocialRelation，不参与家谱推荐。
- 祖父母、舅爷爷、表亲、姻亲等为 DerivedConcept，由路径确定性计算；缺少父母资料但双方确认兄弟姐妹时，可保存直接 SourceFact，不能反推出父母身份。
- 用户原始输入可为“妈妈、老妈、母亲、舅爷爷”等自由文本，不强制理解内部关系码；原文永久保留且不被 Agent 或词典覆盖。
- 称谓解析优先级为个人偏好 > 当前空间词典 > 地区语言包 > 系统标准称谓。同一空间两位不同已确档用户对同一概念使用同一词后，该词成为空间内可推荐别名，保留 revision，无需管理员审核，也不提升为全局模板。
- 推断等级固定为 `determined`（自动刷新 DerivedFact）、`supported`（生成一句话可确认提案）、`ambiguous`（用通俗话追问一次）、`conflicting`（展示冲突，不自动改）。LLM 自报置信度仅用于排序。

### R4. 创建、确档与推荐资格

- 为他人创建档案时，名字和“与创建者的关系”必填；年龄等字段可空且可动态增加；另有一段自由描述供后续提取，但描述本身不是正式事实。
- 创建者选择“不拉入 / HouseholdSpace / LineageSpace”。选择空间时只建立最小化 provisional node 引用，该人物在本人确档前不是 SpaceMember。
- 本人首次登录先确认“这是我”，再以清单审核既有资料与关系；外部录入事实逐条进入 `proposed → confirmed | disputed`。
- 未确档档案不进入任何推荐池。首版只允许基于创建者与被创建者之间已确认、符合矩阵的关系生成卡片，不做全平台陌生人匹配。
- 朋友/同事不触发家庭或家族推荐；伴侣只可在双方确认且允许披露后推荐共同 HouseholdSpace；配偶可推荐共同 HouseholdSpace 或分别申请加入对方选定的 LineageSpace；亲生/收养/继亲亲子与已确认兄弟姐妹按创建时选择推荐；监护默认仅 HouseholdSpace。
- 发送申请必须由用户触发，Agent 只能推荐。空间和家族不因申请、配偶关系或管理员关联而物理合并。

### R5. 可见性与隐私

- HouseholdSpace 正式成员获得 `household_detail`，但凭据、私人 Session、私人 Memory、未公开关系、健康/住址等高敏感信息永不因此共享。
- LineageSpace 只返回必要展示信息：展示名、当前查看者称谓、世代/位置、确档状态、必要关系边和占位头像；其他类别由本人全局偏好与逐空间覆盖决定。
- guest、pending、provisional node 不获得家庭详情。直系关系若不在同一 HouseholdSpace，不再天然获得 full，只能看到摘要或显式授权内容。
- 未成年人默认最小披露，高风险类别不可因 LineageSpace、Agent 推断或管理员角色自动放宽；监护授权、本人年龄变化与撤销必须可审计。
- Assistant、Steward、RAG、搜索、统计、导出都必须调用同一 VisibilityPolicy；任何缓存、索引与导出不得成为绕过路径。

### R6. ActionCard 与写入治理

- ActionCard 是正式状态对象：`pending → viewed → accepted → executed`，以及 `dismissed | expired | superseded` 终态。
- 相同 `card_type + subject + evidence_version + scope` 不得重复建卡。
- 用户接受卡片后，后端以当前身份、空间、事实版本和权限重新校验；过期或变更的证据必须拒绝执行或 supersede。
- Agent 可创建 Hypothesis/Recommendation 和 ActionCard；SourceFact、成员申请发送、空间桥边、公开范围扩大都必须由有权用户显式触发。

### R7. Memory、RAG、行为事件与 Policy Guard

- DomainEvent、BehaviorProjection、RAGKnowledge、AgentContext 四层分离。称谓纠正、提案处理、卡片操作等产品行为直接形成领域事件，不必先变成记忆卡；禁止键盘/鼠标泛监控。
- 原始聊天不得自动进入 RAG。Assistant 只能提出记忆卡，用户确认后选择 `private`、`household` 或 `lineage` 作用域。
- RAG 白名单为确认记忆卡、家族故事、授权文档、本人简介和公共称谓知识；结构化家谱事实始终通过领域工具查询。
- Steward 只读当前空间的已确认共享 RAG，不读私人 Session；不拥有自由形式隐藏长期记忆，只使用正式事实、投影、词典和 Job checkpoint。
- `familygraph-policy-guard` Pi 扩展覆盖 `input`、`tool_call`、`tool_result`、`context`、`before_provider_request`、`agent_settled`；扩展负责早期阻断与脱敏，最终授权仍由 FastAPI PolicyService/RAGGateway 执行。

### R8. Pi Runtime、Provider 与数据边界

- 首版使用 `pi-coding-agent` SDK，不立即 fork；禁用 read/write/edit/bash，只注册版本化 FamilyGraph 领域工具。
- Node Pi Agent Service 作为内部 sidecar。运行临时状态可在 Node，业务数据库只能通过 FastAPI 领域工具访问；禁止任意 SQL，Agent 容器不得挂载生产 SQLite。
- FastAPI 持久化 `agent_sessions`、`agent_messages`、`agent_runs`、`agent_run_events`、`agent_jobs`；SSE 事件可重放，支持 `Last-Event-ID`、`Idempotency-Key`，断线不得重复执行工具副作用。
- 一个 Session 同时最多一个 Run；每个用户最多两个交互 Run；Steward 使用独立的按空间队列。
- 首版支持一个 OpenAI-compatible 云 Provider 和一个可选本地 Provider。平台运营者维护 Provider 与密钥，空间管理员只能在允许列表中选模型和功能开关；不得静默 fallback。
- 策略判断为敏感的内容必须路由本地 Provider；本地不可用时明确拒绝，不得降级发往云端。

### R9. UI 与会话隔离

- 使用全局悬浮助手；桌面为抽屉，移动端为全屏面板。
- 每个 Session 只绑定一个用户和一个空间。切换空间只切换该空间的 Session 列表，不携带上下文、草稿、消息缓存或 RAG scope。
- ActionCard 同时显示在会话消息流与空间待处理区域，两处共享同一服务端状态。
- 登出、身份切换、权限撤销和空间切换必须清理或隔离相关 Pinia/SSE/消息缓存。

### R10. 数据权利与无迁移前提

- Foundation 同时交付未成年人隐私、owner 移交、自助导出/删除/更正、认领争议与审计合同，这些不是 Agent 后补项。
- 因系统尚未部署且无真实数据，不设计旧成员/旧关系在线迁移、双写、回填或灰度兼容；仍需保证从空数据库按迁移链启动并通过回归。
- 受控联网是最后阶段，默认关闭；MatchBroker 跨空间隐私匹配、多空间单会话、任意 shell/文件/MCP 与全平台陌生人推荐不属于首版。

## Child Task Map And Ordering

| 顺序 | 子任务 | 独立出口 |
|---|---|---|
| 1 | `08-26-v2-0-foundation` | 新身份/空间/隐私/数据权利合同及迁移 |
| 2 | `08-26-v2-1-agent-runtime` | Pi sidecar、工具协议、Session/Run/SSE/Provider |
| 3 | `08-26-v2-2-readonly-assistant` | 单空间只读 Assistant 与全局悬浮 UI |
| 4 | `08-26-v2-3-relationship-intelligence` | SourceFact/DerivedFact、路径与称谓系统 |
| 5 | `08-26-v2-4-steward-action-card` | Steward、领域事件、卡片与推荐矩阵 |
| 6 | `08-26-v2-5-memory-rag-policy` | 作用域 Memory/RAG、Context 与 Policy Guard |
| 7 | `08-26-v2-6-controlled-web` | 默认关闭的受控联网、部署与全量治理 |

父子关系不表示依赖；上述顺序和每个子任务的前置条件以各自 PRD/implement 为准。

## Acceptance Criteria

- [ ] AC-P1：七个子任务均具备独立、可测试的 PRD、design、implement、notes、handoff，并保持 `planning`。
- [ ] AC-P2：身份、空间、事实、可见性、Agent、Memory/RAG 与推荐之间只有一个权威合同，不存在 v1 “直系边自动 full”或 owner 删除级联空间的残留语义。
- [ ] AC-P3：任何 Agent 路径都无法直接写 SourceFact、发送加入申请、扩大公开范围、读取其他空间或绕过 VisibilityPolicy。
- [ ] AC-P4：交互 Run 的创建、持久化 SSE、断线续传和幂等重试可证明不会重复执行工具副作用。
- [ ] AC-P5：同一用户不同空间、不同用户同一空间、Assistant 与 Steward 的 Session、Memory、RAG 和工具授权均有隔离测试。
- [ ] AC-P6：关系称谓能保留原始输入，确定性路径可解释，四级称谓优先级及两人使用规则可验证。
- [ ] AC-P7：provisional 档案在确档前不进入推荐池；所有推荐只生成卡片，用户明确动作后才发送申请或写正式事实。
- [ ] AC-P8：云/本地 Provider 策略、敏感强制本地、无静默 fallback、受控联网默认关闭均有失败态验收。
- [ ] AC-P9：桌面与移动端完成全局悬浮助手旅程，空间切换和登出不残留跨 scope 内容。
- [ ] AC-P10：空库部署、迁移、备份恢复、后端/前端/Node 测试和 Docker 全链路验收全部通过。

## Out Of Scope

- 首版不做 MatchBroker 跨空间候选匹配或全平台陌生人推荐。
- 不做物理合并 HouseholdSpace/LineageSpace，不自动桥接伴侣或配偶双方家族。
- 不把自由称谓变成结构真源，不用 LLM 取代确定性亲属计算。
- 不允许 Agent 使用任意 SQL、shell、文件系统、任意 HTTP 或未审计 MCP。
- 不做自动聊天归档到 RAG、隐藏长期记忆、空间之间共享 Session。
- 不做真实生产数据的在线迁移或兼容窗口；当前无用户、无部署、无数据。

## Blocking Open Questions

无。联网 Provider 的具体品牌/模型、地区语言包首批覆盖范围和 embedding 模型属于实现前可配置项，不改变本 PRD 的产品行为。
