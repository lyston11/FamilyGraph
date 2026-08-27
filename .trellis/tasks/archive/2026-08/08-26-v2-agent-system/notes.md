# 决策台账与规划注记

## 已锁定决策

| ID | 决策 |
|---|---|
| V2-D01 | 产品层只有 Assistant 与 Steward 两种 Agent；内部 Extractor/Resolver 是窄工作流。 |
| V2-D02 | Agent 定义属于系统，但数据权限来自本次 user/space scope，不来自系统管理员身份。 |
| V2-D03 | Account、Profile、Fact 分别确档；未确档人物不进入推荐池。 |
| V2-D04 | HouseholdSpace、LineageSpace 物理独立；PersonalFamilyView 仅派生，空间永不自动合并。 |
| V2-D05 | SourceFact 只能由有权人员确认写入；DerivedFact 可由确定性引擎自动刷新。 |
| V2-D06 | 自由称谓原文永久保留；四级优先级为个人、空间、地区、系统。 |
| V2-D07 | 两位不同已确档用户在同一空间使用同一词即可成为空间推荐别名；无管理员发布、无全局模板晋升。 |
| V2-D08 | 首版推荐只覆盖创建者与被创建者，且申请必须由用户触发；MatchBroker 后置。 |
| V2-D09 | Household 成员可看家庭详情；Lineage 仅必要字段+显式公开；直系但不同 household 不自动 full。 |
| V2-D10 | DomainEvent、BehaviorProjection、RAGKnowledge、AgentContext 分层；原始聊天不自动入 RAG。 |
| V2-D11 | 使用 pi-coding-agent SDK sidecar，不 fork、不开放 coding tools、不直接连接业务 SQLite。 |
| V2-D12 | FastAPI 持久化 Session/Run/Event/Job，SSE 可重放且副作用幂等。 |
| V2-D13 | 云端 OpenAI-compatible + 可选本地 Provider；敏感强制本地，无可用本地则拒绝，无静默 fallback。 |
| V2-D14 | 全局悬浮助手，单空间 Session，桌面抽屉、移动全屏。 |
| V2-D15 | v2 先 Foundation 后 Runtime/Assistant/Intelligence/Steward/Memory/Web。 |
| V2-D16 | 当前无部署、用户和数据，不做生产迁移、双写、回填或兼容期。 |

## 关键澄清

- Steward 能“知道”的是当前空间已授权的 SourceFact、DerivedFact、TermRegistry、BehaviorProjection、确认共享 RAG 和 Job checkpoint；它不负责把所有信息都沉淀为自由记忆，也看不到私人 Session。
- Pi 技术上能让自定义工具直连数据库，但本项目故意禁止：Pi 接口能力不等于安全授权边界。领域工具统一复用 FastAPI 的事务、FSM、VisibilityPolicy 与 audit。
- 用户修改其他人物称谓等真实产品行为会直接形成 DomainEvent/BehaviorProjection，不被强迫走记忆卡。
- “完整称谓优先生成，错误后允许用户改”只作用于 DerivedFact/展示词；用户修改不会覆盖结构真源或原始输入。

## 非阻塞后置项

- MatchBroker 的隐私候选令牌与跨空间匹配。
- 多空间单会话、跨空间上下文引用。
- 地区语言包首批具体地区和 embedding 模型选择。
- 受控联网 Provider/搜索供应商品牌。
- 未审计 MCP、任意文件或 shell 能力。

## 证据来源

- `.trellis/HANDOFF.md` 与 `.trellis/spec/architecture.md`：v1 权威合同与 v2 遗留项。
- `research/pi-and-learngraph.md`：Pi 与 LearnGraph 可复用边界。
- `research/current-code-baseline.md`：v1 实际接入点与缺口。
- Trellis memory session `01a037a9-750c-7701-960f-756ebd75e4ba`：本次多轮压力测试与最终接受记录。
