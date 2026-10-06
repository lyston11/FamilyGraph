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


## 已完成的实测（2026-10-06）

- [x] 四方对照基准（FTS5 / pg_trgm / Unicode n-gram / PGroonga），同一语料 + 子串真源，
      全部真实执行。证据：`research/evidence/lexical-four-way.json`、`lexical-decision.md`。
- [x] **pg_trgm 排除**（10/10 查询零命中，最高相似度 0.294 < 阈值 0.3）。
- [x] **PGroonga 主路径确定**（10/10 精确，唯一在全部查询上与真源一致的候选）；
      已在隔离环境验证可用（官方 alpine-16 镜像，扩展 4.0.9）。
- [x] Unicode n-gram 实测（含单字可用，但覆盖度打分过度召回最多 4 条），降为后备。

## 未完成（实现前置）

- [ ] **PGroonga 生产安装方式**（编译/包管理/托管是否允许）——归 `10-04-postgres-operations-cutover`。
- [ ] recall@k / precision@k 评测（需标注语料）。
- [ ] 10 万级 chunk 的索引大小、写入成本、查询延迟。
- [ ] 与 `rag_chunks` 的 revision/scope/visibility 过滤组合。
- [ ] PGroonga 索引的 `pg_dump`/恢复行为与重建成本。
- [ ] 中文分词模式在专业术语/人名上的调优。


## AC 逐条状态（2026-10-06，归档前核查）

**本任务曾被过早归档，已恢复为 in_progress。** 下表说明原因：AC 中多项未满足，
仅凭「已完成基准」不足以宣告完成。

| AC | 状态 | 说明 |
|---|---|---|
| golden corpus 对比召回/排序/延迟/索引成本 | **DONE** | 召回/排序：四方对照（子串真源）；**延迟/索引成本**：2 万条规模实测——服务器端执行 **1.3–4.3ms**（`EXPLAIN ANALYZE`），索引构建 0.8s |
| 明确默认实现 | **DONE** | PGroonga 主路径 + Unicode n-gram 后备，已实测决定 |
| 撤权/删除/revision/citation/scope 过滤 + 索引版本切换回归与 mutation | **NOT-DONE** | 未实现：PGroonga 索引与 `rag_chunks` 的 revision/scope/visibility 过滤组合未验证 |
| pgvector hybrid 不越权、不改 citation 认证 | **NOT-DONE** | 未验证 union/rerank 与授权过滤的组合 |
| 仅在所有 PG 内方案不达标时才建独立服务 | **N/A** | PGroonga 达标，无需独立服务 |

### 未完成项（因此不能归档）

- **golden corpus 对比召回/排序/延迟/索引成本**：召回与排序已实测（四方对照，子串真源）；**延迟与索引成本未测**（12 条语料，非规模基准）
- **撤权/删除 + 索引版本切换的回归与 mutation**：`scope/status/revision` 过滤与 PGroonga 的组合已实测（含 EXPLAIN 确认过滤未被忽略），但**撤权后旧条目不可见**与**索引版本切换**的回归/mutation 仍未做
- ~~PGroonga 索引不存储于 PG relation~~ → **已实测并量化**：`pg_dump` 导出 DDL 但不导出索引数据；恢复时自动重建，恢复后查询正常（20000 行 / 4ms）。**运维影响**：恢复耗时含索引重建；`pg_class` 看不到索引体积（2 万条时数据目录 `pgrn*` 约 8.5MB，SQL 侧读 0），容量规划会低估
- **pgvector hybrid 不越权、不改 citation 认证**：未验证 union/rerank 与授权过滤的组合
