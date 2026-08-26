# FamilyGraph V2.5 Memory、RAG 与 Policy Guard

> 依赖：V2.0–V2.4 完成。

## Goal

为 Assistant 和 Steward 增加可解释、可撤销、严格按用户/空间隔离的长期知识能力，并通过 Pi 扩展与 FastAPI PolicyService 双层阻断隐私泄露、prompt injection、秘密外发和未确认事实升级。

## Requirements

### MR-1 四层分离

- DomainEvent 是实际产品动作真源；BehaviorProjection 是可重建行为投影；RAGKnowledge 是确认且获准检索的材料；AgentContext 是本次模型调用的临时投影。
- Agent summary、Session history、RAG chunk 都不能替代 SourceFact、原始话语或 DomainEvent。
- 目的明确的称谓纠正、提案处理、卡片操作直接进 DomainEvent，不需要记忆卡。

### MR-2 记忆卡

- 原始聊天不自动索引。MemoryCandidateExtractor 可从本轮对话提出候选卡，展示原话范围、摘要、敏感等级、建议 scope 和用途。
- 用户确认后选择 `private`、`household:<space>` 或 `lineage:<space>`；系统不得自动扩大 scope。
- Memory 保留 author、source message/文档、原文引用、confirmation、revision、sensitivity、retention、revoked/deleted 状态。
- 隐私检测器可阻止高风险内容公开，不能替用户静默改为更宽 scope。

### MR-3 RAG 白名单与隔离

- 可索引：确认记忆卡、家族故事、授权文档、本人简介、公共称谓知识；结构化家谱事实走领域工具，不复制为 RAG 真源。
- 首版使用 SQLite FTS5 trigram；embedding 可选且必须沿用同一 metadata/filter，不引入独立向量数据库。
- RAGGateway 在检索前按 actor、space、scope、VisibilityPolicy、confirmation、sensitivity、status 过滤；检索后在返回 Context 前再次过滤。
- 记录必须带作者、原始来源、scope、敏感级别、确认状态、revision 和索引版本；引用可追溯。
- 删除、撤销、membership/disclosure/claim 改变触发索引删除或失效，旧 chunk 不得继续命中。

### MR-4 ContextBuilder

- 构建顺序：身份/space 固定信息 → 权限过滤 → 可信度/敏感度过滤 → relevance 排序 → token budget → provider policy。
- 外部网页、用户文档、RAG 与记忆内容标记为不可信 data，不能注入为 system/tool instruction。
- Assistant 只读取当前用户当前空间允许的 private/shared 知识；Steward 只读当前空间确认 shared 知识，不能读 private。
- context build 记录使用了哪些 source ids/policy/version/排除原因，但不复制敏感全文到审计日志。

### MR-5 `familygraph-policy-guard`

- `input`：敏感、越权意图、unsafe content、prompt injection 初筛。
- `tool_call`：工具 allowlist、scope、参数上限和危险组合，异常 fail closed。
- `tool_result`：二次脱敏、大小限制、未确认事实标签。
- `context`：仅拼接预取的安全 Context，不做重 DB 查询。
- `before_provider_request`：最终 payload secret/PII/本地强制策略检查。
- `agent_settled`：收尾状态、usage/审计投影，不把隐藏内容写日志。
- FastAPI PolicyService/RAGGateway/领域端点仍执行最终授权；扩展不能成为唯一防线。

### MR-6 Provider 与隐私

- 敏感分类为 local_required 的对话、Context 或文档只能走本地 Provider；本地不可用明确拒绝。
- 云 Provider 请求前移除不必要 PII，记录策略决定而非敏感 payload。
- 不允许空间管理员绕过平台敏感策略；可选择关闭功能或使用允许的更严格配置。

## Acceptance Criteria

- [ ] AC-MR1：原始聊天不会自动出现在 RAG；未经确认的候选卡无法被任何 Session 检索。
- [ ] AC-MR2：private/household/lineage 三 scope 对两个用户、两个空间的正反授权测试全部通过。
- [ ] AC-MR3：Steward 读不到 private Session/Memory，只能读当前空间 shared knowledge。
- [ ] AC-MR4：删除/撤销/离开空间/披露收紧后旧 FTS/embedding chunk 无法命中，重建可恢复正确索引。
- [ ] AC-MR5：prompt injection、secret、masked data、未确认事实、超大工具输出在对应 Guard hook 被阻断/降权，FastAPI 再验证仍成立。
- [ ] AC-MR6：Context source/排除原因可追溯，RAG 引用回到原始材料；Agent 摘要不取代原文。
- [ ] AC-MR7：local_required 无本地 Provider 时拒绝，绝不发往云端或静默 fallback。
- [ ] AC-MR8：Context/Guard 热路径满足预算，`context` hook 不执行重 DB 查询。

## Out Of Scope

- 不自动保存所有对话、不做隐藏自由记忆、不做泛用户监控。
- 不把 RAG 当结构化家谱真源，不引入独立向量数据库。
- 不做跨空间共享知识或多空间 Session。

## Blocking Open Questions

无；embedding 模型为可选配置，不阻塞 FTS5 首版。
