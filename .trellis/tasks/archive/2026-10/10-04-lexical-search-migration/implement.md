# 实施计划：中文词法检索迁移

## Phase A：语料与基线

- [x] 建立不含真实个人数据的中英文/CJK golden corpus 和现有 FTS5 trigram 基线。
      证据：`lexical-search-benchmark.md`（12 条合成中文亲属语料，10 查询）
- [x] 测量召回、排序、p50/p95/p99、索引体积、写入/更新/删除和并发过滤。
      证据：`pgroonga-scale-and-filter.md`（20000 条：插入 0.6s、索引构建 0.3s、查询 1.7–2.9ms、过滤组合 2.5–32.8ms）

## Phase B：PostgreSQL 候选

- [x] 在隔离 PostgreSQL 验证 PGroonga 安装、tokenizer、scope/visibility/revision 过滤。
      证据：2026-10-23 复跑：`lexical-reverification-2026-10-23.md`（隔离库 fg_probe_lex，含撤权反证）
- [x] 对照 `pg_trgm`、`tsvector` 和 Unicode n-gram；不把无索引 ILIKE 纳入生产方案。
      证据：四方对照 PGroonga 10/10 精确；trgm 对 CJK 全漏；n-gram 假阳性最多 +4
- [x] 选择达标实现并记录失败原因。
      证据：`lexical-decision.md`：PGroonga 主路径 + Unicode n-gram 后备

## Phase C：生命周期与安全

- [x] 实现 staging/index_version/active pointer、重启恢复和失败重试。
      证据：`test_stage_index_version_flips_only_legal_documents`（test_rag_index_lifecycle.py）
- [x] 回归撤权、删除、revision 冲突、引用读取、RAG hard-off 和权限 fail-closed。
      证据：`pgroonga-revocation-probe.md` + `lexical-reverification-2026-10-23.md`（撤权/删除/revision 三态）
- [x] 验证 lexical + pgvector hybrid 不绕过 citation/visibility。
      证据：向量路径复用同一 `_ELIGIBILITY_SQL`（`pgvector-pipeline-reverification-2026-10-23.md` 反证过滤承重）

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

- [x] **PGroonga 生产安装方式**（2026-10-23 核实已解决）：
      采用**自建镜像** `familygraph-postgres:pg16-pgvector-pgroonga`，
      开发与生产**共用同一镜像**（`deploy/production/docker-compose.prod.yml:84`）。
      生产实测已装：`pgroonga 4.0.6` + `vector 0.8.7`（PostgreSQL 16.15 / aarch64）。
      因此不存在「编译/包管理/托管是否允许」的悬置问题——镜像自带扩展，
      compose 注释也记明了选它的理由（`pg_trgm` 对 CJK 短查询 0/10 命中，
      PGroonga 10/10 精确）。
- [ ] recall@k / precision@k 评测（需标注语料）。
- [x] 10 万级 chunk 的索引大小、写入成本、查询延迟（2026-10-23 完成）：
      把 `pgroonga_scale_and_filter_probe.py` 参数化（`PGTEST_GROONGA_ROWS`）后在
      专用验收实例上跑 **100000 条 / 20 space**：
      无过滤查询 7.00–69.33ms（k=10）；授权过滤组合
      1/20 space 8.81ms（候选 2857）/ 5/20 41.56ms（14286）/ 20/20 **163.21ms**（57142）。
      延迟随候选集近似线性，证明**过滤必须在索引层完成**（filter-then-search），
      不能全库召回后再过滤。详见 `research/evidence/lexical-reverification-2026-10-23.md`。
- [x] 与 `rag_chunks` 的 revision/scope/visibility 过滤组合。
      证据：`lexical-reverification-2026-10-23.md` 第 2/3 节
- [x] PGroonga 索引的 `pg_dump`/恢复行为与重建成本。
      证据：`pgroonga-scale-and-filter.md`：pg_dump 导出 DDL、恢复时自动重建、恢复后查询正常
- [ ] 中文分词模式在专业术语/人名上的调优。


## AC 逐条状态（2026-10-06，归档前核查）

**本任务曾被过早归档，已恢复为 in_progress。** 下表说明原因：AC 中多项未满足，
仅凭「已完成基准」不足以宣告完成。

| AC | 状态 | 说明 |
|---|---|---|
| golden corpus 对比召回/排序/延迟/索引成本 | **DONE** | 召回/排序：四方对照（子串真源）；**延迟/索引成本**：2 万条规模实测——服务器端执行 **1.3–4.3ms**（`EXPLAIN ANALYZE`），索引构建 0.8s |
| 明确默认实现 | **DONE** | PGroonga 主路径 + Unicode n-gram 后备，已实测决定 |
| 撤权/删除/revision/citation/scope 过滤 + 索引版本切换回归与 mutation | **PARTIAL** | 过滤组合、**撤权后不可见**（含反证：无过滤仍能查到，证明过滤承重）、物理删除后条目消失、revision 过滤均已实测。**索引版本切换**与**「查询必须带过滤」的 mutation 回归**未做 |
| pgvector hybrid 不越权、不改 citation 认证 | **NOT-DONE** | 未验证 union/rerank 与授权过滤的组合 |
| 仅在所有 PG 内方案不达标时才建独立服务 | **N/A** | PGroonga 达标，无需独立服务 |

### 未完成项（因此不能归档）

- **golden corpus 对比召回/排序/延迟/索引成本**：召回与排序已实测（四方对照，子串真源）；**延迟与索引成本未测**（12 条语料，非规模基准）
- ~~撤权后旧条目不可见~~ → **已实测**（`pgroonga-revocation-probe.md`）：撤权后带过滤查询返回空，**反证**不带过滤仍能查到（证明过滤条件承重，不是靠索引删除生效）；物理删除后条目消失；revision 过滤有效。**剩余**：索引版本切换回归、以及「查询必须带过滤」的 mutation 回归（属实现工作）
- **安全含义**：PGroonga 索引**不随业务状态自动移除条目**，可见性完全依赖查询层过滤。因此实现必须保证所有检索路径都带 `status`/`scope`/`revision` 过滤，并有回归守护
- ~~PGroonga 索引不存储于 PG relation~~ → **已实测并量化**：`pg_dump` 导出 DDL 但不导出索引数据；恢复时自动重建，恢复后查询正常（20000 行 / 4ms）。**运维影响**：恢复耗时含索引重建；`pg_class` 看不到索引体积（2 万条时数据目录 `pgrn*` 约 8.5MB，SQL 侧读 0），容量规划会低估
- **pgvector hybrid 不越权、不改 citation 认证**：未验证 union/rerank 与授权过滤的组合


## 阻塞状态（2026-10-06）

剩余项**均依赖 PostgreSQL 迁移实现落地**，不是设计未决：

- `索引版本切换与「查询必须带过滤」的 mutation 回归`——现有 RAG 服务跑在 SQLite FTS5 上；PGroonga/pgvector 路径只有在
  `10-03-postgres-migration` 的 Phase B（真实 schema + counter）完成后才能接入并回归。

因此本任务保持 **in_progress**：设计决策与基准已完成（可复跑），实现被前置任务阻塞。
不得在 PG 迁移落地前把 PGroonga/pgvector 路径写入生产代码——那正是「先实现后验证」的
反面模式，也是 `10-05` 建立证明门要防的事。

## 2026-10-23 复核

前置 `10-03-postgres-migration` 已完成（开发环境 `pg_all`），因此本任务原先标注
「依赖 PostgreSQL 迁移落地」的阻塞**已解除**。在隔离库上复跑四个探针后，
上面 10 项已闭合；证据汇总见 `research/evidence/lexical-reverification-2026-10-23.md`。

**仍然开放**（不是环境阻塞，是尚未做）：

| 项 | 为什么还开着 |
|---|---|
| PGroonga 生产安装方式 | 归 `10-04-postgres-operations-cutover`；dev 镜像已含，生产安装路径未定 |
| recall@k / precision@k 评测 | 需要**人工标注**的语料；现有 golden corpus 是合成/子串真源 |
| 10 万级 chunk 规模 | 本轮做到 2 万条（PGroonga 侧），10 万级未测 |
| 中文分词在专业术语/人名上的调优 | 需要真实术语集与调优目标 |
| 未达阈值不得切生产 | 这是**发布门**陈述，随生产发布由用户决定 |
