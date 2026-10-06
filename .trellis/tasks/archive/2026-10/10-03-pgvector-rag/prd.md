# pgvector 优先的 RAG 检索扩展

## Goal

在 PostgreSQL 主存储确定后评估并原型化 pgvector；保留 Memory/RAG 的 source、revision、scope、visibility、citation、撤权和可重建索引合同，只有证据证明不足时才引入独立向量数据库。

## Scope

- embedding row 与 source/document/chunk、revision、space/scope、visibility policy version 的绑定。
- pgvector ANN/过滤/延迟/规模基准，embedding 生成与索引更新的低优先级 background budget。
- revision、删除、撤权、重建、失败重试和旧向量不可见行为。
- 对比 pgvector 与独立向量库的容量、过滤、运维、故障和一致性成本；输出是否拆独立 vector DB 子任务的证据。
- Steward 仍不绕过现有 projection/授权边界接入私有 RAG。

## Dependencies / non-dependencies

- 依赖父任务接受的 PostgreSQL 真源；最好在 PostgreSQL schema/事务实验确认后进行。
- 依赖现有 `memory-rag-execution-contract` 与 `rag-index-lifecycle-contract`；不依赖 Redis。
- embedding/index 任务不得占用 control-plane 保留容量，也不得改变 Agent provider/tool 合同。

## Boundary with lexical search

`pgvector` 只负责语义候选，不替代当前 SQLite FTS5 trigram 的中文词法能力。`10-04-lexical-search-migration` 独立评估 PGroonga 与 Unicode n-gram；两者可以在 PostgreSQL 内组成 hybrid retrieval，但必须共享 source/revision/scope/visibility/citation 和 index-version 合同。未完成词法基准前，不得把 `tsvector` 或无索引 `ILIKE` 当作 pgvector 的配套默认方案。

## 实测证据（2026-10-06）：ANN 必须 filter-then-ANN

`scripts/migration-proof/pgvector_filter_probe.py` 在真实 pgvector 0.8.7 上实测
（2000 行 / 维度 64 / k=10 / HNSW）：

| 过滤条件 | post-filter 剩余 | 精确应有 |
|---|---|---|
| 无过滤 | 10 | 10 |
| 允许 5/10 空间 | **5** | 10 |
| 允许 1/10 空间 | **1** | 10 |
| 允许不存在的空间 | **0** | 0 |

**post-filter 会静默返回不足 k 的结果**：允许 1/10 空间时只有 1 条，而 RAG 无法区分
「无相关内容」与「被授权过滤掉」。filter-then-ANN 在两种选择性下都取满 k=10。

因此本任务的硬约束：**必须先按 scope/visibility/revision 过滤再向量排序**，
且过滤列必须有索引。证据：`research/evidence/pgvector-filter-probe.md`。

**未覆盖**：真实 embedding 分布、IVFFlat 对比、10 万级规模、与 lexical 的 union/rerank。

## Acceptance Criteria

- pgvector 原型能按 scope/visibility/revision/citation 正确查询，撤权/删除后旧向量不可见且可回收。
- embedding 失败、索引重建、版本切换和 PostgreSQL/worker 重启均可恢复，不产生半切换状态。
- 有真实规模与延迟基准；未达到阈值前不引入独立向量库。
- 若 pgvector 不足，给出独立服务的持久事实、授权同步、故障回退和迁移设计，不直接接入生产。
- RAG 关闭、来源失效、版本冲突和引用读取回归保持 fail-closed。
