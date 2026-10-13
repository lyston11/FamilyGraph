# 技术设计：pgvector 优先的 RAG 检索扩展

## 1. 数据模型

embedding 记录必须绑定 `source/document/chunk`、`source_revision`、`index_version`、`space_id/scope`、visibility policy version、content hash 和模型版本。向量是可重建索引，不是正文、授权或引用事实；正文与 ExactChunkRef 继续由 PostgreSQL RAG 合同管理。

## 2. 查询边界

查询先建立当前 execution identity 和 authorized projection/filter，再做向量相似度排序；禁止用向量相似度发现隐藏空间、目标或来源。返回结果必须带服务端生成的 citation/handle，按现有 context build、预算和引用合同进入模型上下文。Steward 暂不接私有聊天 RAG。

## 3. 生命周期

来源 revision、撤权、删除和 policy 变化使旧向量不可见；后台 job 再异步回收。embedding/index update 使用低优先级、每 space 有界的 background budget，不占 control-plane。index version 切换先 stage 完整集合和基准，再以原子指针切换；失败保持旧 active version。

## 4. pgvector-first 评估

第一阶段在 PostgreSQL 中评估 exact/ANN index、过滤组合、数据量、更新/删除、并发写入、查询 p50/p95/p99、备份恢复和运维成本。只有 pgvector 在目标规模或过滤/延迟/隔离要求上明确不满足，才设计独立向量服务；该服务也不能成为业务事实来源，必须有授权同步、revision 绑定、故障回退和重建路径。

## 6. 与 PostgreSQL 词法检索的边界

pgvector 不替代当前 FTS5 trigram 词法检索。PostgreSQL 迁移采用 PGroonga 作为中文/日文词法检索首选；若部署不允许扩展，再评估应用 Unicode n-gram 倒排表。`tsvector`/`pg_trgm` 只能作为经过 golden corpus 证明的辅助路径，不能未经基准直接替换当前 CJK 语义。

Hybrid retrieval 的固定顺序是：授权 scope/visibility projection → lexical 与 vector 候选 → union/rerank → 最终 revision/citation 验证。向量索引失效只能禁用 vector 候选，不得放宽词法检索或授权。

RAG/embedding 关闭、来源失效、revision 冲突、授权不可证明时 fail closed 或回退确定性/无向量路径，不伪造命中。PostgreSQL/worker 重启、embedding provider timeout、重复 job 和部分索引失败可恢复，不产生半切换 active version。
