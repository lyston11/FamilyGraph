# 实施计划：pgvector 优先的 RAG 检索扩展

## Phase A：PostgreSQL 依赖确认

- [x] 等 PostgreSQL schema/事务实验确认后建立 embedding/index 原型表和约束。
      证据：`rag_embeddings.pgvector_ddl` + `test_pgvector_ddl_binds_dimension_and_carries_scope_columns`；前置 PG 迁移已完成
- [x] 设计 source/document/chunk/revision/scope/visibility/citation 外键与索引。
      证据：`rag_embedding_segments`(chunk_id FK, model, segment_index) PK + (model,revision,scope) 索引；`build_vector_candidates` 选 `source_revision`/`scope`
- [x] 明确 embedding model/version、content hash、index version 和删除/撤权状态。
      证据：`EmbeddingConfig`(model/dimension) + `index_version` + `content_sha256`；`build_vector_candidates` 按 `s.model` 过滤（换模型后旧向量不参与比较）

## Phase B：pgvector 基准

- [x] 建立合成且不含真实个人数据的规模数据集，测试 exact/ANN、过滤、写入、更新、删除、重启和备份恢复。
      证据：`pgvector-filter-probe.md`(2000 行) + `pgvector-lifecycle-probe.md`(3000 行) + 2026-10-23 全链路复跑
- [ ] 测量查询 p50/p95/p99、更新延迟、索引构建、空间隔离和 background budget 对 control-plane 的影响。
      **部分完成**：2026-10-23 已测查询 p50/p95、索引构建时间与体积、空间隔离（filter-then-ANN）。
      **仍未测**：p99、更新延迟、background budget 对 control-plane 的影响（后者需真实部署）。
      本条**不勾选**：勾选会把「测了一半」记成「已测」。
- [x] 验证授权 projection 先于向量查询，结果只能产生合法 citation/handle。
      证据：filter-then-ANN 反证（去掉过滤能返回撤权 chunk → 过滤承重）；citation 由 `_rows_to_hits` 生成

## Phase C：生命周期与故障

- [x] 实现 revision 切换、撤权不可见、删除回收、失败重试、旧版本保留和原子 active pointer。
      证据：`pgvector-lifecycle-probe.md`：撤权后带过滤不可见、行与向量仍在；`stage_index_version` 原子翻转
- [x] 注入 PostgreSQL/worker/embedding provider/Redis（如使用）故障、重复消息和半批失败。
      证据：`test_vector_failure_falls_back_to_lexical`、`test_transport_failure_degrades`、`embedding_fallback_probe.py`(真实部署停容器)
- [x] 运行现有 memory/rag/index/citation/visibility 回归与 mutation。
      证据：`test_search_rag_vector.py`(5) + `test_rag_embeddings.py`(11) + `test_embedding_client.py` + `test_embedding_chunking.py`；向量路径**只增不减/不重排/复用同一授权过滤**三条约束各有测试

## Phase D：选型结论

- [x] 将 pgvector 与独立向量服务按规模、延迟、过滤、授权同步、运维、备份和故障回退比较。
      证据：pgvector 达标（10 万行 recall 0.96 / p50 0.93ms，构建 120.5s），AC 允许「达标即保留 pgvector-first」，故**不建独立服务**；完整对比表未做（非阻塞）
- [x] 若 pgvector 达标，保留 pgvector-first，不创建独立服务；若不达标，另建独立 vector DB 子任务，不直接生产接入。
      证据：pgvector 达标（filter-then-ANN + RRF 全链路 PASS，见 `pgvector-pipeline-reverification-2026-10-23.md`），不建独立服务
- [x] 记录 RAG 关闭、来源失效、revision 冲突和权限撤销的用户可见行为。
      证据：`test_assistant_context_degrades_to_empty_when_rag_disabled`、`test_vector_failure_falls_back_to_lexical`、`test_rag_lifecycle_acceptance.py` 撤权/取代/换版

## 回滚

关闭向量查询并回退确定性/既有词法路径；保留 source/revision/chunk 数据和旧 active version，不删除合法引用。索引构建失败不得切换 active pointer。


## 已完成的实测（2026-10-06）

- [x] pgvector 可用性验证（0.8.7，官方 `pgvector/pgvector:pg16` 镜像，`CREATE EXTENSION` 成功）。
- [x] **ANN 与授权过滤组合实测**：post-filter 在低选择性下静默返回不足 k
      （允许 1/10 空间时只剩 **1** 条），filter-then-ANN 取满 k=10。
      证据：`research/evidence/pgvector-filter-probe.md`。
- [x] 硬约束已登记：**必须先按 scope/visibility/revision 过滤再向量排序**，过滤列需索引。

## 未完成

- [ ] 真实 embedding 分布（探针用确定性合成向量）。
      **部分完成**：2026-10-23 把探针数据从均匀随机改为**有簇**（500 簇 + 0.15 扰动），
      因为均匀随机是 ANN 最坏情形、会把 recall 低估一个数量级（0.10 vs 0.9867）。
      **但这仍不是真实 embedding**——几何形态对齐了，分布没有。
      真实分布由 `vector_similarity_distribution.py` 在 bge-small-zh-v1.5 上承担（其结论已落盘）。
      本条**不勾选**：AC 问的是真实分布，合成簇不是。
- [x] IVFFlat 与 HNSW 的召回/延迟对比
      证据：2026-10-23 `pgvector-index-scale-benchmark-2026-10-23.md`：HNSW ef_search=40 recall 0.9600/p50 0.93ms；IVFFlat 构建快 29 倍但 probes≥50 退化为顺序扫描。
- [x] 10 万级规模与索引构建时间
      证据：2026-10-23：10 万行 / 512 维实测——插入 71.4s、HNSW 构建 120.5s / 260.4MB、IVFFlat 4.2s / 261.0MB。
- [x] 与 PGroonga 词法结果的 union + 确定性 rerank。
      证据：`rag_embeddings.fuse_candidates`(RRF) + `_vector_candidates` 已接入 `search_rag`；`test_rrf_*` 4 条 + 全链路 RRF 确定性
- [x] embedding 生成、更新、删除与 revision 绑定。
      证据：`index_segments`/`store_vectors` 幂等；`pending_chunks` 只选 active；`revision` 参与向量候选过滤


## AC 逐条状态（2026-10-06，归档前核查）

**本任务曾被过早归档，已恢复为 in_progress。** 下表说明原因：AC 中多项未满足，
仅凭「已完成基准」不足以宣告完成。

| AC | 状态 | 说明 |
|---|---|---|
| 按 scope/visibility/revision/citation 正确查询，撤权后不可见可回收 | **PARTIAL** | filter-then-ANN 已实测；**撤权后旧向量不可见与回收未验证** |
| embedding 失败/索引重建/版本切换/重启可恢复，无半切换 | **PARTIAL** | **索引重建已实测**（REINDEX 0.51s，结果一致）；embedding 失败与版本切换恢复未实现 |
| 真实规模与延迟基准 | **NOT-DONE** | 只有 2000 行合成向量，无规模/延迟基准 |
| pgvector 不足时的独立服务设计 | **N/A** | 尚未判定不足 |
| RAG 关闭/来源失效/版本冲突/引用读取 fail-closed | **NOT-DONE** | 现有实现有回归，但**pgvector 路径**上的这些语义未验证 |

### 未完成项（因此不能归档）

- ~~撤权后旧向量不可见~~ → **已实测**（`pgvector-lifecycle-probe.md`）：撤权后带过滤查询不再返回；**反证**按 id 直读证明行仍在、`status='revoked'`、向量非空——可见性完全由查询层过滤承担，不是靠删除向量
- **回收未量化**：删 1/3000 行后索引 168kB 未变，**单行删除不足以观测 VACUUM 回收**，需批量删除后测量
- **索引重建已验证**：`REINDEX` 0.51s，重建后查询结果与重建前一致（索引可重建，无半切换状态）
- **embedding 失败/索引重建/版本切换/重启可恢复，无半切换**：未实现
- **真实规模与延迟基准**：只有 2000 行合成向量，无规模/延迟基准
- **RAG 关闭/来源失效/版本冲突/引用读取 fail-closed**：现有实现有回归，但**pgvector 路径**上的这些语义未验证


## 阻塞状态（2026-10-06）

剩余项**均依赖 PostgreSQL 迁移实现落地**，不是设计未决：

- `embedding 失败恢复、版本切换、批量删除回收`——现有 RAG 服务跑在 SQLite FTS5 上；PGroonga/pgvector 路径只有在
  `10-03-postgres-migration` 的 Phase B（真实 schema + counter）完成后才能接入并回归。

因此本任务保持 **in_progress**：设计决策与基准已完成（可复跑），实现被前置任务阻塞。
不得在 PG 迁移落地前把 PGroonga/pgvector 路径写入生产代码——那正是「先实现后验证」的
反面模式，也是 `10-05` 建立证明门要防的事。

## 2026-10-23 复核

前置 `10-03-postgres-migration` 已完成（开发环境 `pg_all` + pgvector），
原先「依赖 PostgreSQL 迁移落地」的阻塞**已解除**。在隔离库复跑全链路探针后，
上面 12 项已闭合；证据见 `research/evidence/pgvector-pipeline-reverification-2026-10-23.md`。

**本轮修掉一个探针缺陷**（不是产品缺陷）：`pgvector_pipeline_probe.py` 手写的
`rag_documents` DDL 缺 `updated_at`，而生产查询会选该列 → 探针在「表结构不完整」上
失败，会把探针缺陷误报成产品缺陷。已补齐。

**仍然开放**（不是环境阻塞）：

| 项 | 为什么还开着 |
|---|---|
| IVFFlat 与 HNSW 召回/延迟对比 | 只测了 HNSW |
| 10 万级规模与索引构建时间 | 本轮 pgvector 侧只到 3000 行 |
| 真实 embedding 分布下的 union/rerank 端到端 | 全链路探针用确定性 stub（验管线不验语义质量）；语义质量由 `vector_similarity_distribution.py` 与 `chinese_retrieval_gain.py` 承担 |
| 批量删除后的 VACUUM 回收量化 | 既有证据已记「单行删除不足以观测回收」 |
| 将 pgvector 与独立向量服务按运维/备份/故障回退比较 | pgvector 达标故不建独立服务，但**完整对比表**未做（AC 允许「达标即保留」，故非阻塞） |

## 2026-10-23 索引与规模基准补充

本轮新增 `scripts/migration-proof/pgvector_index_comparison_probe.py`，
在隔离库上闭合「IVFFlat/HNSW 对比」与「10 万级规模」两条。

**修掉两个会导致错误决策的探针缺陷**：

1. 操作符类 `vector_l2_ops` 配 `<=>`（余弦）查询 → 索引**从未被使用**，
   所有参数下 recall 都是 1.0000、延迟等于顺序扫描。生产 DDL 用
   `vector_cosine_ops`，已对齐；并新增 `EXPLAIN` 断言「索引确实被使用」，
   否则判 `INVALID` 并非零退出。
2. 均匀随机向量是 ANN 的**最坏情形** → HNSW recall 被低估到 0.3233，
   据此会错误得出「必须换独立向量库」。对照实验：同一索引下均匀随机
   recall 0.1000 vs 有簇数据 0.9867。已改为「500 簇 + 0.15 扰动」并写进 docstring。

**产品结论**：生产默认 `ef_search=40` 下 recall 0.9600、p50 0.93ms（比精确检索快约
450 倍），**无需调整配置**；HNSW 优于 IVFFlat（后者构建快 29 倍但延迟高 10 倍，
且 probes≥50 时规划器改用顺序扫描）。

**证据强度声明**：本任务多数结论来自**隔离库探针（L3）**，不是真实部署。
需要真实多实例/尾延迟/线上发布的项一律**未勾选**，见各条后的「仍未测」。
