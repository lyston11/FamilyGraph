# pgvector：ANN 与授权过滤组合探针

由 `scripts/migration-proof/pgvector_filter_probe.py` 生成（真实执行）。

- 表 2000 行、维度 64、k=10、HNSW 索引
- **post-filter**（ANN 后过滤）在低选择性下返回少于 k；
- **filter-then-ANN**（先过滤再排序）能取满。

因此 RAG 查询必须先按 scope/visibility 过滤再向量排序。

## 未覆盖

- 未测真实 embedding 分布（本探针用确定性合成向量）；
- 未测 IVFFlat 与 HNSW 的召回/延迟对比；
- 未测 10 万级规模与索引构建时间；
- 未测与 lexical 结果 union/rerank 的组合。
