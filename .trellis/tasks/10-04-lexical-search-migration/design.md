# 技术设计：中文词法检索迁移

## 1. 责任分层

PostgreSQL RAG 表保存 source/document/chunk/revision/scope/visibility/citation；词法索引和向量索引都是可重建派生物。授权先于检索，最终结果仍由服务端生成 ExactChunkRef/citation。

## 2. 候选实现

首选 PGroonga extension，以多语言/CJK 语义提供词法候选；`pg_trgm` 只作为英文/兼容性对照，`tsvector` 不作为中文等价实现。若 PGroonga 无法部署或 golden corpus 不达标，使用应用生成的 NFKC Unicode n-gram 倒排表，并为 ngram、index_version、chunk_id 建索引。

## 3. Hybrid

lexical top-K 与 pgvector top-K 合并、去重、确定性重排；候选阶段可以使用相似度，最终必须再次校验 scope、visibility、revision、source 状态和 citation。旧 active index 在新版本 staging 完整验证前保持可用。

## 4. 生命周期

revision/撤权/删除先使旧索引不可见，再异步回收；index version 采用 stage → validate → 原子 pointer switch。失败、重启、重复 job 和 worker 中断均不得切换半成品版本。
