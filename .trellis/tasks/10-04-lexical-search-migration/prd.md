# 中文词法检索迁移与 PGroonga 基准

## Goal

为 SQLite FTS5 trigram 设计 PostgreSQL 词法检索迁移，并在不降低中文召回、授权和引用安全的前提下与 Unicode n-gram 后备方案比较。

## Requirements

- 词法检索不能直接替换为普通 `tsvector` 或无索引 `ILIKE`；必须保留 source/document/chunk、revision、scope、visibility、citation 合同。
- 评估 PGroonga 作为 PostgreSQL 扩展；若部署或基准不接受，评估应用维护 Unicode n-gram 倒排表。
- 词法候选必须先经过 execution identity 和授权 projection/filter；向量检索不能发现隐藏空间或来源。
- 词法与 pgvector 语义候选可 hybrid union/rerank，但最终引用和权限由 PostgreSQL 再确认。
- RAG 关闭、来源撤权/删除、revision 冲突、索引构建失败和 worker/PostgreSQL 重启必须 fail-closed 或可恢复，不产生半切换 active version。

## Dependencies

依赖 postgres-migration 的 PostgreSQL schema/事务实验和现有 `memory-rag-execution-contract`、`rag-index-lifecycle-contract`；不依赖 Redis。PGroonga 不应成为第二持久事实系统。

## Acceptance Criteria

- golden corpus 对比 FTS5 trigram、PGroonga、`pg_trgm`/`tsvector` 和 Unicode n-gram 的中文/英文召回、排序、延迟和索引成本。
- 明确默认实现：PGroonga 达标则采用 PGroonga，否则采用 Unicode n-gram；不以无界 `ILIKE` 作为生产后备。
- 撤权、删除、revision、citation、scope/visibility 过滤和索引版本切换有回归与 mutation 证据。
- pgvector hybrid 查询不越权、不改变 citation 认证；Steward 仍不接入私有聊天 RAG。
- 若所有 PostgreSQL 内方案均不达标，才新建独立搜索/向量服务任务，不直接接入生产。
