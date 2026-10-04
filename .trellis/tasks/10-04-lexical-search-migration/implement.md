# 实施计划：中文词法检索迁移

## Phase A：语料与基线

- [ ] 建立不含真实个人数据的中英文/CJK golden corpus 和现有 FTS5 trigram 基线。
- [ ] 测量召回、排序、p50/p95/p99、索引体积、写入/更新/删除和并发过滤。

## Phase B：PostgreSQL 候选

- [ ] 在隔离 PostgreSQL 验证 PGroonga 安装、tokenizer、scope/visibility/revision 过滤。
- [ ] 对照 `pg_trgm`、`tsvector` 和 Unicode n-gram；不把无索引 ILIKE 纳入生产方案。
- [ ] 选择达标实现并记录失败原因。

## Phase C：生命周期与安全

- [ ] 实现 staging/index_version/active pointer、重启恢复和失败重试。
- [ ] 回归撤权、删除、revision 冲突、引用读取、RAG hard-off 和权限 fail-closed。
- [ ] 验证 lexical + pgvector hybrid 不绕过 citation/visibility。

## Gate

- [ ] 未达到质量/延迟/运维阈值不得切生产；若 PG 内方案均不达标，另建独立服务任务而非直接接入。
