# 词法检索在真实 PostgreSQL + PGroonga 上的复跑（2026-10-23）

## 为什么补这一轮

`10-04-lexical-search-migration` 与 `10-03-pgvector-rag` 的剩余项都写着
「依赖 PostgreSQL 迁移落地」。该前置**已完成**（开发环境已在 `pg_all`），
但两个任务的 checklist 未随之更新，剩余项长期挂在 `[ ]`。

本次在**开发服务器的隔离 database** 上复跑四个探针（`fg_probe_lex`，跑完即 DROP），
把「前置已完成」从推断变成实测。

**环境**：`fg-dev-pg`（PostgreSQL 16 + pgvector + pgroonga），探针自建 `rv_*`/`grn_*`/`lc_*`
表，不碰业务 schema。**线上未接触。**

## 1. 四方对照：PGroonga 精确，trgm 对 CJK 完全不可用

`lexical_four_way_compare.py`（同时传 `PGTEST_DSN` 与 `PGTEST_DSN_GROONGA`）。
**注意**：只传后者会让 PGroonga 列显示 `n/a`——这不是「PGroonga 不可用」，
而是该探针要求两个 DSN 都存在。第一次运行时踩到过，已在证据中记明。

| 查询 | 真源 | FTS5原生 | FTS5+LIKE | trgm | n-gram | **PGroonga** |
|---|---|---|---|---|---|---|
| 爷 | 1 | 缺1 | 精确 | 缺1 | 精确 | **精确** |
| 爷爷 | 1 | 缺1 | 精确 | 缺1 | 精确 | **精确** |
| 叔叔 | 1 | 缺1 | 精确 | 缺1 | 多1 | **精确** |
| 叔父 | 1 | 缺1 | 精确 | 缺1 | 多3 | **精确** |
| 家族 | 3 | 缺3 | 精确 | 缺3 | 多1 | **精确** |
| 家族树 | 2 | 精确 | 精确 | 缺2 | 多2 | **精确** |
| 家族树关系 | 1 | 精确 | 精确 | 缺1 | 多4 | **精确** |
| 亲属 | 2 | 缺2 | 精确 | 缺2 | 多4 | **精确** |
| 舅舅 | 1 | 缺1 | 精确 | 缺1 | 精确 | **精确** |
| 外祖父 | 1 | 精确 | 精确 | 缺1 | 多2 | **精确** |

**结论：PGroonga 10/10 与真源精确一致。**
`pg_trgm` 对中文**全部漏检**（相似度低于阈值），已可排除。
Unicode n-gram 召回全但**假阳性严重**（最多多 4 条）——因此它的定位是
**后备**而非主路径，与 `lexical-decision.md` 的既有决策一致。

## 2. 撤权不可见 + 过滤承重性反证

`pgroonga_revocation_probe.py`：

```text
初始：带过滤 -> [3]；无过滤 -> [3, 4]
撤权 id=3 后：带过滤 -> []；无过滤 -> [3, 4]   ← 索引条目仍在
  OK 撤权后带过滤查询不再返回该 chunk
  OK 反证成立：不带过滤仍能查到 id=3，说明过滤条件是承重的，不是靠索引删除生效
revision 推进到 2 后（按 revision=2 过滤）-> [3]
物理删除 id=4 后，无过滤查询 -> [3]（不含 4）
PASS
```

**这条反证是承重的**：它证明「撤权后查不到」不是因为索引里没有该条目，
而是因为查询层的 `status` 过滤生效。PGroonga 索引**不随业务状态自动移除条目**，
可见性完全由查询层承担——所以实现必须保证所有检索路径都带过滤。

## 3. 规模与过滤组合

`pgroonga_scale_and_filter_probe.py`（20000 条合成 chunk，20 个 space）：

```text
插入 0.6s，PGroonga 索引构建 0.3s
表+索引总计 3208 kB（PGroonga 索引不在 PG relation 内）
网络基线（SELECT 1 往返）0.13ms

无过滤查询延迟（k=10）：爷爷 1.97ms / 叔父 1.70ms / 家族树 2.86ms
                        亲属 2.63ms / 外祖父 1.74ms / 爷 1.81ms

授权过滤组合：
  filter-then-search (1/20 space)  命中 0   候选 571    2.50ms  ← 该 space 内无匹配文本
  filter-then-search (5/20)        命中 10  候选 2858   8.82ms
  filter-then-search (20/20)       命中 10  候选 11428  32.78ms
```

**低选择性会静默返回不足 k**（1/20 时命中 0）：这不是查询缺陷，而是该空间内确实没有
匹配文本。但它是一个**必须在实现层处理**的语义——「候选被授权过滤光」与「语料里没有」
在用户可见层面是不同的事，这正是 `RetrievalOutcome.filtered_out` 要区分的。

延迟随候选集线性增长（571→11428 候选对应 2.5→32.8ms），说明**过滤必须在索引层完成**
（filter-then-search），不能全库召回后再过滤。

## 4. 这两个任务的剩余项据此更新

| 任务 | 原剩余项 | 结论 |
|---|---|---|
| lexical | 索引版本切换回归 | `test_stage_index_version_flips_only_legal_documents` 已覆盖；**已满足** |
| lexical | 「查询必须带过滤」的 mutation 回归 | **本次反证已提供**（不带过滤仍查得到 → 过滤承重） |
| lexical | pgvector hybrid 不越权 | 由 `10-03-pgvector-rag` 的授权过滤复用同一 `_ELIGIBILITY_SQL` 承担；见 `rag-authorized-corpus-matrix.md` |
| pgvector | embedding 失败/版本切换恢复 | 见 `embedding_fallback_probe.py`；未在本轮复跑 |
| pgvector | 真实规模与延迟基准 | 本轮 PGroonga 侧已做（20000 条）；**pgvector 侧未做** |
| 两者 | PGroonga 在 dev 安装 | **已完成**：dev 镜像为 `familygraph-postgres:pg16-pgvector-pgroonga`，`/ready` 报 `extensions.pgroonga: true` |

## 5. 环境清理

```text
fg_probe_lex：已 DROP（pg_database 中无 %probe% 残留）
开发库 familygraph：未受影响（agent_runs 1510 行不变）
线上：未接触
```


## 附：10 万级规模复跑（2026-10-23，专用验收实例）

把 `pgroonga_scale_and_filter_probe.py` 参数化（`PGTEST_GROONGA_ROWS`）后，
在专用验收实例（`fg-acc-pg` 55460）上跑 **100000 条 / 20 space**：

```text
网络基线（SELECT 1 往返，取最小值）：0.12ms

=== 查询延迟（无过滤，k=10）===
  爷爷   命中 10   69.33ms
  叔父   命中 10    7.36ms
  家族树 命中 10   13.18ms
  亲属   命中 10   12.11ms
  外祖父 命中 10    7.29ms
  爷     命中 10    7.00ms

=== 授权过滤组合（关键 AC）===
  filter-then-search (1/20 space)  命中 0   候选  2857    8.81ms
  filter-then-search (5/20)        命中 10  候选 14286   41.56ms
  filter-then-search (20/20)       命中 10  候选 57142  163.21ms
```

**结论**：延迟随候选集近似线性增长（2857 → 57142 候选对应 8.81 → 163.21ms），
因此**过滤必须在索引层完成**（filter-then-search），不能全库召回后再在应用层过滤。

`1/20 space` 时命中 0 是**该空间内确实没有匹配文本**，不是查询缺陷——
但实现层必须区分「候选被授权过滤光」与「语料里没有」（`RetrievalOutcome.filtered_out`）。

注：输出里「延迟含 SSH 隧道往返（约 180ms 基线）」那行是探针的旧文案，
与本次实测的 0.12ms 网络基线不一致，已按实测值记录。
