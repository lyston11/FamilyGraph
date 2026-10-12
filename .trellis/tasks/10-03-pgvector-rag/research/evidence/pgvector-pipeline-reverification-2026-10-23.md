# pgvector 全链路在真实 PostgreSQL 上的复跑（2026-10-23）

## 为什么补这一轮

`10-03-pgvector-rag` 的剩余项写着「依赖 PostgreSQL 迁移落地」。该前置**已完成**
（开发环境 `pg_all`，dev 镜像 `familygraph-postgres:pg16-pgvector-pgroonga`），
但 checklist 未随之更新。本次在**开发服务器的隔离 database** 上复跑全链路探针。

**环境**：`fg_probe_vec`（PostgreSQL 16 + pgvector），跑完即 DROP。
**线上未接触。**

## 1. 探针缺陷（先修才能验）

第一次运行失败：

```text
psycopg.errors.UndefinedColumn: column d.updated_at does not exist
```

**这不是产品缺陷**，是探针自己手写的最小 DDL 与真实 schema 脱节：

- `pgvector_pipeline_probe.py` 的 `CREATE TABLE rag_documents` 只有
  `(id, source_type, source_id, scope, sensitivity, revision, status)`；
- 而 `rag_embeddings.build_vector_candidates` 的 SQL 会选
  `d.updated_at AS source_updated_at`。

核实真实 schema（dev `information_schema.columns`）确认 `rag_documents`
**确有** `updated_at`：

```text
id source_type source_id source_revision author_account_id owner_user_id space_id
scope sensitivity confirmation_status revision visibility_snapshot
visibility_snapshot_key index_version content_sha256 status invalidated_at
invalidation_reason created_at updated_at
```

**危害**：探针会在「表结构不完整」上失败，而不是在它真正要验证的管线上失败——
把探针缺陷误报成产品缺陷，或反过来让后来者以为 pgvector 路径坏了。

**修法**：探针 DDL 补 `updated_at timestamptz not null default now()`，
并让 INSERT 显式写入该列。

## 2. 修复后的实测结果

```text
索引报告: {examined: 4, indexed: 4, segments_written: 6, skipped_degraded: 0,
           failed: 0, batches: 2, degraded_reason: None, errors: []}
  OK  长文本被切分：6 个分段覆盖 4 个 chunk
  分段向量行数 6，覆盖 4 个 chunk（期望 4：撤权文档不产生新向量）
  带过滤候选 [1, 5, 2, 3]（期望不含撤权 chunk 4；每 chunk 只出现一次）
  反证（无过滤）[1, 4, 5, 2, 3]
  OK  过滤条件承重
  RRF 融合 [2, 1, 3]（两路都命中的 chunk 2 应排第一）
  OK  RRF 确定性
PASS: pgvector 索引 + filter-then-ANN（含反证）+ RRF 融合
```

四条合同各自被证实：

| # | 合同 | 证据 |
|---|---|---|
| 1 | 索引写入幂等、按 `pending_chunks` 只选 active | `indexed: 4, failed: 0`，重复调用结果一致 |
| 2 | 撤权文档**不产生新**向量 | 6 分段覆盖 4 chunk（撤权的 chunk 4 无新向量） |
| 3 | **filter-then-ANN 承重** | 带过滤不含撤权 chunk 4；**反证**去掉过滤能返回它 |
| 4 | RRF 融合确定性 | 两路都命中的 chunk 2 排第一，重复调用结果一致 |

第 3 条的反证是承重的：它证明「撤权后查不到」不是因为向量不存在，而是查询侧过滤
生效。探针专门**手工插入撤权 chunk 的历史向量**来证明这一点——否则「撤权文档没有
向量」会让过滤看起来可有可无。

## 3. 与既有决策的关系

`vector_similarity_distribution.py` 已给出 `_VECTOR_MIN_SIMILARITY = 0.50` 的取值依据，
并证明**余弦相似度不能作相关性判据**（相关与不相关分布重叠：不相关最高 0.7712 >
相关最低 0.5091）。因此向量**不做重排**，只做「补充候选」，与
`memory_rag` 中三条硬性约束（只增不减 / 不重排已有命中 / 复用同一授权过滤）一致。

换 embedding 模型后地板必须重测，不能沿用 0.50。

## 4. 环境清理

```text
fg_probe_vec：已 DROP（pg_database 中无 %probe% 残留）
临时探针目录 /tmp/fg-probe：已删除
开发库 familygraph：未受影响（agent_runs 1510 行不变）
线上：未接触
```

## 5. 仍未做的（诚实声明）

| 项 | 说明 |
|---|---|
| IVFFlat 与 HNSW 召回/延迟对比 | 只测了 HNSW |
| 10 万级规模与索引构建时间 | 本轮 pgvector 侧只到 3000 行；PGroonga 侧到 20000 行 |
| 真实 embedding 分布下的 union/rerank 端到端 | 本探针用确定性 stub（验证管线，不验证语义质量） |
| 批量删除后的 VACUUM 回收量化 | 既有证据已记「单行删除不足以观测回收」 |
| 独立向量服务的对比 | pgvector 达标，无需独立服务（`prd.md` 的 AC 已满足） |
