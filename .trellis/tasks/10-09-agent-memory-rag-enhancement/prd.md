# Agent 记忆模型与 RAG 检索质量强化

## Goal

在**不放松**现有 source/revision/scope/visibility/citation/fail-closed 合同的前提下，把
Assistant 的记忆与 RAG 从「能检索到」提升到「记得对、更新得对、用得对」：

- 记忆能表达**变化与取代**（旧事实被新事实取代，而不是并存矛盾）；
- 检索在中文词法之外有**可控的语义召回与确定性重排**，且授权过滤永远先于排序；
- 上下文组装按**分层预算**而非贪心填满，长片段不再挤掉全部短命中；
- 每条改动都有**可复现的中文 golden set 与五能力指标**，不能靠「感觉更好」交付。

## Background

现有实现已具备完整的安全骨架（`memory-contract`、`memory-rag-execution-contract`、
`rag-index-lifecycle-contract` 三个 spec 为证），且实测确认了两条硬结论：

1. **检索索引不承载授权**——撤权只改主表状态，索引条目仍在（PGroonga 与 pgvector 双向反证）；
2. **ANN 必须 filter-then-ANN**——post-filter 在低选择性下静默返回不足 k。

因此本任务只做**质量增量**，不动安全骨架：所有新增路径必须复用同一 eligibility 过滤与
`_rows_to_hits` 引用投影。

## Scope

- 记忆的生命周期语义：supersede / 冲突 / 时间有效区间 / 过期与退役。
- 记忆获取：规则提取的扩展与 LLM 精炼（仍走候选 → 用户确认，不自动入库）。
- 受控记忆工具（agent 可提议记忆，不可确认）。
- 检索：hybrid union 与确定性 rerank；查询计划的增量（实体锚定、多查询），保持 closed alias 表。
- 上下文组装：分层预算与排序函数。
- 索引：chunking 策略与 `index_version` 回填（复用 `RAGIndexMaintenanceState`）。
- 评估：中文 golden set 与五能力指标（信息提取 / 多会话推理 / 时序推理 / 知识更新 / 弃答）。

## Non-goals

- 不改授权模型、不加新的可见性来源、不绕过 `ContextBuilder` 或 provider gateway。
- 不引入独立向量库（由 `10-03-pgvector-rag` 的证据决定）。
- 不做 GraphRAG / 通用知识图谱抽取；家族图谱已是结构化事实源，记忆层不复制它。
- 不在本任务内实现 PostgreSQL 迁移（`10-03-postgres-migration` 是硬前置）。

## Dependencies

- 硬前置：`10-03-postgres-migration`（hybrid 检索、批量回填、真正的向量路径只能在 PG 上落地）。
- 相邻：`10-03-pgvector-rag`（filter-then-ANN 与 embedding 生命周期）、
  `10-04-lexical-search-migration`（PGroonga 词法主路径）。
- 合同：`backend/memory-contract`、`backend/memory-rag-execution-contract`、
  `backend/rag-index-lifecycle-contract`。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| AC-1 | 存在可复现的中文 golden set（含时序、更新、多跳、弃答四类），能在隔离环境一条命令跑出五能力分数，且分数与代码版本绑定。 |
| AC-2 | 记忆支持 supersede：新事实确认后旧事实不再进入检索结果，但历史仍可审计、可回溯；冲突无法判定时不静默择一。 |
| AC-3 | 检索重排在 golden set 上相对当前基线有可测量提升，且词法-only 路径的行为与今日逐字一致（不回归）。 |
| AC-4 | 上下文组装按分层预算执行；单条长片段不能挤掉全部其它命中；预算与纳入/排除原因可解释、可审计。 |
| AC-5 | 任何新增检索/记忆路径都通过 mutation 验证：去掉 eligibility 过滤或引用复核时测试必须失败。 |
| AC-6 | LLM 参与的任何环节（提取精炼、查询改写、rerank）失败时回退到确定性路径，且不产生未确认的记忆或伪造的引用。 |

## 未决问题

- supersede 的判定由谁做：用户显式、规则、还是模型提议 + 用户确认？（见 design.md 决策 1）
- rerank 是否值得引入 cross-encoder 服务（延迟 vs 质量），取决于 golden set 上确定性重排的余量。
