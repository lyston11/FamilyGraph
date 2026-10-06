# pgvector：ANN 与授权过滤组合探针

由 `scripts/migration-proof/pgvector_filter_probe.py` 生成（真实执行）。

- 表 2000 行、维度 64、k=10、HNSW 索引
- **post-filter**（ANN 后过滤）在低选择性下返回少于 k；
- **filter-then-ANN**（先过滤再排序）能取满。

因此 RAG 查询必须先按 scope/visibility 过滤再向量排序。

## 实测数字（2000 行 / 维度 64 / k=10 / HNSW）

| 过滤条件 | ANN top-k | 过滤后剩余 | 精确应有 |
|---|---|---|---|
| 无过滤 | 10 | 10 | 10 |
| 允许 5/10 空间 | 10 | **5** | 10 |
| 允许 1/10 空间 | 10 | **1** | 10 |
| 允许不存在的空间 | 10 | **0** | 0 |

**post-filter 的失败模式**：允许 1/10 空间时只返回 **1 条**（应为 10 条）——
用户看到的不是「结果不精确」，而是「几乎没有结果」，而 RAG 无法区分
「没有相关内容」与「被过滤掉了」。允许不存在的空间时返回 0 条，看起来与
「无匹配」完全一样。

**filter-then-ANN 在两种选择性下都取满 k=10**（子集分别有 200 与 400 行）。

## 对 RAG 设计的硬约束

1. **必须先按 scope/visibility/revision 过滤，再向量排序**；
2. 过滤列（`space_id`、`scope`、`status`）必须有索引，否则 filter-then-ANN 退化为全扫；
3. 若无法先过滤（例如过滤条件无法表达为 SQL 谓词），必须**over-fetch 后重试**
   （逐步增大 k），而不是接受不足 k 的结果——但 over-fetch 的上界需要独立基准。

## 与 lexical 的关系

本探针只覆盖向量路径。`lexical-decision.md` 已确定词法主路径为 PGroonga；
两者的 union/rerank 组合未在本探针中验证。

## 未覆盖

- 未测真实 embedding 分布（本探针用确定性合成向量）；
- 未测 IVFFlat 与 HNSW 的召回/延迟对比；
- 未测 10 万级规模与索引构建时间；
- 未测与 lexical 结果 union/rerank 的组合。
