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


## 实测基准（2026-10-06）：pg_trgm 已可排除

由 `scripts/migration-proof/lexical_search_benchmark.py` 在隔离 PostgreSQL + SQLite 上
真实执行（12 条合成中文亲属语料，10 个查询）。证据：
`research/evidence/lexical-search-benchmark.md`。

| 方案 | CJK 短查询（≤2 字） | 排序 | 结论 |
|---|---|---|---|
| FTS5 trigram（现状） | `MATCH` 零命中 → 靠 LIKE 后备 | `bm25` | 现状基线 |
| **pg_trgm `%`** | **10/10 查询零命中**（最高相似度 0.294 < 阈值 0.3） | 仅 `similarity()` | **排除** |
| pg_trgm LIKE | 可用（与 FTS5+LIKE 逐行一致） | **无** | 仅可作加速，不可作主路径 |
| PGroonga | **未测**（隔离容器无此扩展） | 有评分 | 候选（需先安装） |
| Unicode n-gram 倒排表 | 设计可控 | 需自实现 | 候选 |

**pg_trgm 对 CJK 不可用的机制**：`show_trgm('家族')` 只产生 3 个哈希 trigram
（`{0x2f0bbe,0x854422,0x8b8c13}`），英文 6 字符有 7 个——CJK trigram 集合稀疏导致
相似度被稀释，全部低于默认阈值。降低阈值会让不相关文本命中，且 `similarity()` 缺少
`bm25` 的词频/长度归一化。

**因此本任务的主路径只剩两个候选**：PGroonga 或应用维护的 Unicode n-gram 倒排表。
`pg_trgm` 只能作为 `LIKE` 的索引加速层，不能承担排序与召回。

### 实施前置

- PGroonga 需要在目标 PostgreSQL 集群**安装扩展**（当前隔离容器
  `pg_available_extensions` 中不存在，需编译安装）——这是运维决策，归
  `10-04-postgres-operations-cutover` 的部署面。
- Unicode n-gram 倒排表需要实现 + 规模基准（写放大、索引膨胀）。

### 仍未覆盖

- PGroonga 的真实召回/延迟（未安装，未测）；
- Unicode n-gram 的实现与基准（只有设计）；
- recall@k / precision@k 需要标注语料，本基准只有命中数；
- 12 条合成语料不代表真实家庭资料分布。
