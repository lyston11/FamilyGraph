# Agent Runtime assistant-only 收口

## Goal

在保留 FamilyGraph 两个正式 Agent 的前提下，收紧“会话式/Pi Agent Runtime”边界：

- Assistant 是面向用户的交互式 LLM Agent，唯一进入 `AgentSession`、`AgentRun`、`AgentJob`、service/run token、Node sidecar lease 和通用工具执行协议的运行时 Agent。
- Steward 是事件驱动、按空间分区、长期运行的底层引擎 Agent，负责关系/称谓/个人家族视图的持续规划、推荐资格判断和一致性审计；它继续通过 `StewardJob`、maintenance 和自身的空间策略边界运行。

本任务消除 generic `AgentJob(kind="steward")` 的遗留入口，但不得把 Steward 从产品架构、`StewardJob` 调度链路、或 policy/RAG 的 Steward consumer 身份中删除。这样可以避免两套执行入口混淆，同时保留未来实现 Steward Agent 能力的正确边界。

## Product roles and confirmed decisions

### Assistant

Assistant 负责浏览器会话式交互：理解用户问题、查询当前用户有权访问的家族资料、解释 Steward 的派生结果、收集明确确认和受限反馈。它通过 FastAPI 领域工具和 ProviderGateway 工作，不能绕过服务端授权。

Assistant 的 generic Runtime 对象是：

```text
AgentSession → AgentRun ↔ AgentJob → assistant sidecar lease → Pi session
```

Assistant 允许唯一的受限写入例外 `record_term_usage`：只有服务端校验 session consent、run scope、工具 allowlist 和 `(run_id, tool_call_id)` 幂等后，才能记录明确同意的称谓使用信号；它不能修改人物、关系、成员资格或 SourceFact。

### Steward

Steward 是另一个正式 Agent，但不是 Assistant 的聊天会话，也不是 generic `AgentJob` 的另一个 kind。它在一个较大的内部关系图上，按空间和策略边界为每个用户规划不同的 PersonalFamilyView：

- 不同用户可以共享部分节点，也可以拥有对方没有的亲属分支；
- 通过配偶或其他显式关系可以连接不同家族树，但连接不自动扩大可见权；
- 同一治理空间中的人物不因同空间自动属于某用户的个人家族树；
- Steward 只基于当前空间、确认事实、授权共享知识和有效桥接计算派生结果；
- Steward 可以生成 DerivedFact、称谓结果、候选、ActionCard 和审计发现，但不能自行创建成员资格、升级可见权、确认 SourceFact、发送申请或合并空间。

Steward 的正式运行对象是：

```text
DomainEvent → StewardJob(space_id, job_id, policy_version)
            → maintenance / Steward engine → projections and ActionCards
```

当前任务不实现 PersonalFamilyView、全局关系图连接算法、新认领用户触发或 Pi Steward child run；PersonalFamilyView 已拆为 `09-01-personal-family-view`，新认领用户触发已拆为 `09-01-new-user-family-recommendations`。但本任务必须保留它们所依赖的 Steward 身份、空间和 shared-only policy 合同。

## Background / repository evidence

- 浏览器入口 `backend/app/api/agent.py` 创建 session 时已经使用 `assistant`；没有合法的 generic `AgentJob(kind="steward")` 产品入口。
- `backend/app/models/steward.py` 和 `backend/app/services/steward.py` 已提供独立的 `StewardJob`、每空间 active job 约束、lease/heartbeat/reaper、checkpoint、ActionCard 和确定性执行链路。
- `backend/app/services/memory_rag.py` 已按 `agent_kind` 区分 private/public 与 household/lineage shared 数据：Steward 可以消费授权的当前空间 shared 数据，但不得消费 private Session/Memory 或其他空间数据。
- 当前 WIP 使用单一 `AGENT_KINDS` 限制 generic runtime 和 `ContextBuilder`。如果直接把它收窄为 `("assistant",)`，会错误地拒绝 Steward 作为 policy/RAG consumer，并破坏 private 隔离合同。
- 旧迁移 `0009_agent_runtime.py` 仍允许 `assistant|steward`，并为 generic steward job 建立空间级 active partial index。
- 当前 WIP 已包含后端模型/队列/schema/token/tool registry、internal API、Node sidecar 测试和迁移 `0024_agent_runtime_assistant_only.py` 的 assistant-only 改动；它尚需按本 PRD 纠正 Steward policy consumer 边界、补齐测试、同步文档并完成独立验证。
- `0024` 的既定安全方向是：升级前检查 generic runtime 表中的 steward/未知/null kind，发现冲突就中止，不静默删除、改写或转换历史行。

## Requirements

### R1. Separate runtime and consumer kinds

- 定义并使用两个不同的概念：
  - `RuntimeAgentKind`：generic session/run/job/lease/token/sidecar，只允许 `assistant`；
  - `PolicyConsumerKind`：需要在 Context/RAG/可见性策略中表达数据消费者身份，至少允许 `assistant` 和 `steward`。
- 不用一个全局常量同时表达上述两种集合；命名必须让调用者看出其边界。
- `StewardJob` 是 Steward 的独立执行单元；不得用 `AgentRun`、`AgentJob` 或 Assistant service token 替代它。
- 未来若引入 Pi Steward child run，必须另立任务定义其父子作业、凭据、上下文和审计合同，不得恢复 `AgentJob(kind="steward")`。

### R2. Assistant generic Runtime fail-closed

- `AgentSession`、`AgentRun`、`AgentJob` 的产品语义、Python 类型、数据库 CHECK 和服务层输入只允许 `assistant`。
- enqueue、lease、heartbeat、settle、run token 签发/解码、internal API 和浏览器 Agent API 对非 assistant/未知/null kind fail-closed。
- internal lease 请求必须显式携带 `kind="assistant"`；省略字段不得再默认降级为 Assistant。
- 非法 kind 必须在 queue 写入或消费前被拒绝，不能依赖数据库异常、空队列或隐式转换。
- Assistant 的每 session active run 和每 account 并发限制继续有效；generic queue 删除 Steward 专用并发限制和 `AGENT_STEWARD_SPACE_BUSY` 语义。

### R3. Steward Agent identity and independent execution

- 保留 `StewardJob`、maintenance pump、空间级 active job 唯一性、lease/heartbeat/reaper、checkpoint 和事件触发链路。
- Steward 的执行上下文始终绑定 `space_id + job_id + policy_version`，并且只读当前空间允许的 confirmed/authorized shared 数据、派生投影、TermRegistry、受限 BehaviorProjection 和 checkpoint。
- Steward 不读取 private Session/Memory、其他空间、platform operator 的全局身份权限，也不通过 Assistant token 或 generic lease 执行。
- Steward 生成的 DerivedFact、PersonalFamilyView 组件、称谓候选、推荐候选、冲突/缺口和 ActionCard 都是派生结果；本任务不改变其业务算法。
- Steward 不自动确认 SourceFact、创建 SpaceMember、扩大可见性、发送申请或合并 LineageSpace。

### R4. Policy/RAG/context consumer boundary

- `ContextBuilder`、`search_rag`、RAG policy 和相关审计模型在需要记录消费者身份时继续支持 `steward`，但必须保持 shared-only 和当前 space 过滤。
- Steward consumer 不得通过 `agent_kind` 取得 private scope、public unrestricted scope、其他空间数据或平台管理员数据。
- Assistant 的 private/public 既有规则、Provider local-required 规则、VisibilityPolicy 复核和 source-author visibility 复核不得回退。
- 如果 ContextBuild 的持久化模型只绑定 generic `AgentRun`，本任务不为 Steward 伪造 AgentRun；Steward 的未来模型上下文持久化另行设计。当前至少保证 policy/RAG consumer 入口和测试不会因 assistant-only 收口而误拒绝合法 Steward shared-only 读取。

### R5. Tool and protocol surface

- 工具注册表、allowlist、tool execute 和 sidecar dispatch 只服务 Assistant generic Runtime。
- 移除 `familygraph.steward_ping` 现行注册/allowlist/dispatch 表面；旧名称只能作为“未知工具被拒绝”的回归输入，不得保留兼容别名。
- 统一使用 `required_kind` 表达工具所需的 generic Runtime kind；需要 Steward 的后台能力不通过 Assistant tool registry 暴露。
- sidecar 对 lease/context 中非 assistant、未知或畸形 kind fail-closed，在拒绝前不得启动 Pi session、Provider 请求或工具 dispatch。
- 保留 `record_term_usage` 的明确 consent、scope、审计和 tool-call 幂等语义；同步修正“所有工具只读”和“V2.4 之前没有去重表”等过期描述。

### R6. Migration and data safety

- `0024_agent_runtime_assistant_only` 在空库和合法 assistant-only 存量库上成功，并保留 generic runtime 业务数据、FK、active-run 唯一约束、scope immutable trigger、事件和工具台账关系。
- 升级前检查 `agent_sessions.agent_kind`、`agent_runs.kind`、`agent_jobs.kind`；出现 steward、未知或 null 时 fail-closed，报告表/行/kind，原数据库保持可恢复，禁止静默删除、转写或生成重复执行记录。
- downgrade 仅恢复历史 schema 的可表达性和旧索引以满足迁移测试，不重新开放现行 API、token、queue 或 sidecar 的 Steward 入口。
- 迁移测试必须覆盖空库、合法数据、冲突数据和必要的 downgrade/升级往返；数据库隔离使用临时 `DATA_DIR`，不得污染开发库。

### R7. Documentation and evidence

- `.trellis/spec/backend/agent-runtime.md`、架构说明、后端/sidecar 注释和测试名称明确表达“双 Agent、两种执行边界”，不得把 Steward 描述为不存在的 generic runtime kind，也不得把 Steward 降格为普通工具函数。
- 文档必须明确 PersonalFamilyView、跨树连接、配偶边和推荐的后续实现依赖 Steward，但不在本任务中实现。
- 测试覆盖：assistant-only generic runtime、StewardJob 独立运行、Steward shared-only policy consumer、非 assistant API/token/tool/sidecar 拒绝、合法 assistant 端到端协议、迁移冲突保护和 `record_term_usage` 幂等。
- internal 协议相关验收包含 Docker Compose 真实联调，不以单侧 mock 代替跨层合同证明。

## Acceptance Criteria

- [ ] **AC-1 双 Agent 口径**：任务文档、架构 spec、模型/服务注释明确 Assistant 与 Steward 都是正式 Agent；Assistant 是唯一 generic Pi Runtime，Steward 使用 `StewardJob`/maintenance。
- [ ] **AC-2 Runtime single kind**：generic `AgentSession`、`AgentRun`、`AgentJob`、service/run token、internal lease 和 sidecar 现行协议只产生/接受 assistant；代码搜索不再发现合法的 generic Steward 创建或租赁路径。
- [ ] **AC-3 Steward preserved**：`StewardJob` 的入队、空间唯一 active job、lease/heartbeat/reaper、执行、checkpoint 和 ActionCard/DerivedFact 结果仍能运行；没有被 assistant-only 常量或迁移误删。
- [ ] **AC-4 Policy consumer preserved**：`ContextBuilder`/RAG policy 能识别 Steward consumer，并证明 Steward 只能读取当前 space 的 confirmed/authorized shared 数据，不能读取 private、其他空间或 operator 全局数据；不得伪造 `AgentRun`。
- [ ] **AC-5 Backend fail-closed**：非 assistant kind 在 generic enqueue、lease、token、tool registry/execute 和 internal API 边界得到预期错误；省略 lease kind 也被拒绝；拒绝不产生错误的 Run/Job/Provider/工具执行副作用。
- [ ] **AC-6 Sidecar fail-closed**：sidecar 对非 assistant 或畸形 lease/context 投影拒绝，且测试证明不会启动 Pi session、Provider 请求或 tool dispatch；合法 assistant lease/context/tool 链路保持可用。
- [ ] **AC-7 Tool surface**：`familygraph.steward_ping` 不在现行注册表/allowlist/dispatch；`required_kind` 两侧一致；`record_term_usage` 的 consent、scope、审计和 `(run_id, tool_call_id)` 幂等不回退。
- [ ] **AC-8 Migration safety**：迁移在空库和合法 assistant-only 存量库通过；冲突 kind 在 DDL 前 fail-closed 并保留可恢复数据；迁移链、临时 DATA_DIR 和 downgrade 测试通过。
- [ ] **AC-9 Documentation consistency**：不再有“Steward 是 generic runtime kind”“所有工具只读”“V2.4 才有 tool_call 去重表”等误导现行实现的文字；后续 PersonalFamilyView/新用户推荐范围明确记录但不被本任务偷做。
- [ ] **AC-10 Quality gate**：backend pytest/mypy/ruff/format、agent type-check/lint/test/build 和 Docker Compose internal 协议联调通过；结果写入任务检查材料后才允许收尾。

## Out of scope

- 不实现 PersonalFamilyView 的完整数据模型、全局关系图组件划分、跨树连接算法或个人树展示 API；已拆为 `09-01-personal-family-view`。
- 不实现“新认领用户”的触发、初始化或陌生人/家族用户推荐；已拆为 `09-01-new-user-family-recommendations`。
- 不实现 Pi Steward child run、Steward 专用 Provider、LLM prompt 或新的 Steward tool protocol。
- 不改变 `StewardJob`、StewardEngine、ActionCard、DerivedFact、DomainEvent、TermRegistry 或 maintenance 的既有业务算法；只修复其与 generic runtime 边界的冲突。
- 不处理 ProviderGateway、Controlled Web、RAG 索引算法或前端 Agent store 的独立设计，除非 assistant-only 合同造成直接编译/协议影响。
- 不迁移真实生产数据，不删除或静默改写冲突历史；未来 generic Steward 历史数据的处置另立任务。
- 不恢复 `steward_ping`，也不以兼容别名或 hidden allowlist 重新暴露 Steward generic queue。

## Deferred items

- 新认领用户何时触发个人家族初始化和推荐：`09-01-new-user-family-recommendations`。
- PersonalFamilyView 的完整模型、跨 Household/Lineage 连接、配偶桥接和“属于某用户的树”计算：`09-01-personal-family-view`；本任务只保护其 policy consumer 和执行边界。
- 如果未来 Steward 需要模型辅助：采用确定性关系/权限核心 + 受限模型候选/排序/解释；模型不能直接改变 SourceFact、权限或成员资格。

## Blocking open questions

无。已确认：

- internal lease `kind` 显式必填为 `assistant`；
- `RuntimeAgentKind` 与 `PolicyConsumerKind` 分离；
- Steward 保留正式 Agent 身份和独立 `StewardJob` 链路；
- 同空间不自动等于个人家族树成员；个人树和推荐受事实、空间、桥接和 VisibilityPolicy 约束；
- 配偶可形成树间连接，但不自动穿透配偶侧亲属分支；
- 新认领触发独立延期。
