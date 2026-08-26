# Relationship Intelligence 规范（V2.3）

> 权威来源：`backend/app/services/{source_facts,relationship_graph,relationship_resolver,derived_facts,terms,intake_extractor}.py`、`app/schemas/kinship.py`、迁移 0010–0012。任务工件：`.trellis/tasks/08-26-v2-3-relationship-intelligence/`。

## 分层与写入权限

- **SourceFact** 是唯一结构真源：类型 biological/adoptive/step parent-child、guardian、spouse、partner、direct_sibling；state 走 proposed→confirmed/disputed/revoked FSM，每次变更 revision+1 并写 domain_events（source_fact.confirmed 等）。当前**不存在**任何外部 HTTP 写入口——写入治理（提案确认流）属 V2.4 Steward。
- **SocialRelation** 独立存储，永不参加血缘/姻亲图。
- **DerivedFact** 是可删重建缓存：viewer+target+space+concept_code，带 path_json、evidence_hash、algorithm_version；读取必须比对 hash，过期行自动重算，绝不返回旧结论。
- **raw_relation_inputs** append-only，不可变性由 DB 触发器 `trg_raw_relation_inputs_immutable` 强制；任何词典/Agent/解析产物不得覆盖原文。
- extractor / TermRegistry / Agent 工具任何路径都**不得写 SourceFact**（AC-KI4）。

## 概念码与路径

- concept_code 为代数编码（如 `Um-Um` 爷爷、`Uf-Um` 外公、`Um-Uf` 奶奶、`Um-Uf-Bm` 舅爷爷）：U/D 上下行 + m/f 性别 + B/W 边类型（血/姻），父系母系方向可区分。
- 图只消费 confirmed 且 (space_id 匹配 OR 全局 NULL) 的事实；可见节点 = active 成员 ∪ active space_profile_refs ∪ 本人，再经 `visibility.evaluate(purpose=agent)` 单点判定。
- 多路径按最少确认边 → 较少不确定边 → 稳定 ID 次序选主路径；环/事实不足不强行给唯一结论。direct_sibling 不虚构父母。

## 四级推断与 Extractor

- ProfileIntakeExtractor 是纯确定性词素解析（无 LLM）：四级 resolution——determined（确认路径完全证明）/ supported（码合法无路径→一句话提案）/ ambiguous（多候选→恰一个追问）/ conflicting（与确认事实矛盾→冲突列表）。
- LLM 自报置信度不提升写入权限；后端永远重新验证候选。

## TermRegistry 优先级与晋升

- 解析优先级：personal > space > locale > system；同一人物跨空间可显示不同称谓。
- 个人纠正立即生效并写领域事件，不需要 Memory card。
- 空间别名晋升：同空间两位**不同 identity_confirmed account** 对同一 concept 用同一词 → 自动 space_suggested；撤销 usage 重算资格（可降级）；不复制到 locale/system，管理员无审批。

## Kinship API 与隐私红线

- 全部端点受 `RELATIONSHIP_INTELLIGENCE_ENABLED` flag（关闭一律 503 KINSHIP_FLAG_DISABLED）；resolve/usages 要求 active 成员；`from_user_id` 强制等于登录者本人。
- **防存在性泄露（V2.3 check 修复的教训）**：`found=false` 时 fact_state 必须全零且不再查询配对事实计数——否则可通过探测得知不可见人物的存在与事实状态。「同一形状」合同必须逐字段成立。
- parse API 每次调用先落 raw_relation_inputs 再返回结果。

## Assistant 工具

- V2.3 新增三工具@1：resolve_free_text_relation（只读）、get_term_alternatives（只读）、record_term_usage（**同意门控**：description/prompt 双处声明必须先获用户明确同意；source_event 服务端固定 assistant_query）。
- get_relationship_path / explain_structural_path 升 @2 并保留 @1 兼容声明（supported_versions 列表模式）。
- 双侧 schema 收敛流程照旧：backend agent_tools.py 注册表为权威，sidecar TypeBox 与 frontend types 三方对齐；涉及 internal 协议必须 Compose 真实联调（stub openai-compatible Provider 容器即可走通 enqueue→lease→context→session 构造→settle 全链路）。
