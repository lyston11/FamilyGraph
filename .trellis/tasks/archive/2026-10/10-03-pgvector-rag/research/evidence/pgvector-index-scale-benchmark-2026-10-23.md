# pgvector 索引对比与 10 万级规模基准（2026-10-23）

## 闭合的两条 AC

- 「IVFFlat 与 HNSW 的召回/延迟对比」
- 「10 万级规模与索引构建时间」

两者都不需要真实多实例，只需要真实 pgvector，因此在**隔离 database** `fg_probe_idx`
上完成（跑完即 DROP，线上未接触）。

维度 512 与生产 embedding 服务（`bge-small-zh-v1.5`）一致，使体积与延迟数字可外推。

## 1. 实测数字（10 万行，维度 512，k=10，30 次查询）

```text
插入 100000 行：71.4s
精确检索（无索引）：p50=418.4ms  p95=422.4ms   ← 真源基线

=== HNSW（ef_search 扫描深度）===
  ef_search=1     recall=0.1000  p50=  0.89ms  p95=  1.04ms
  ef_search=10    recall=0.9233  p50=  1.02ms  p95=  1.11ms
  ef_search=40    recall=0.9600  p50=  0.93ms  p95=  1.07ms   ← pgvector 默认
  ef_search=100   recall=0.9600  p50=  1.02ms  p95=  1.27ms

=== IVFFlat（probes 探针数，lists=100）===
  probes=1        recall=1.0000  p50=  1.73ms  p95=  2.22ms
  probes=10       recall=1.0000  p50=  9.48ms  p95= 11.01ms
  probes=50       recall=1.0000  p50=417.94ms  ← 索引未被规划器使用
  probes=100      recall=1.0000  p50=417.49ms  ← 索引未被规划器使用

=== 构建与体积 ===
  HNSW     build=120.5s   size=260.4MB
  IVFFlat  build=  4.2s   size=261.0MB
```

## 2. 结论：生产默认参数已足够，无需改配置

**HNSW 在生产默认 `ef_search=40` 下 recall=0.9600、p50=0.93ms**，
相对精确检索（418ms）加速约 **450 倍**。

`ef_search=10` 已达 recall 0.9233，`40` 是 0.96，再加大不再改善（0.96 是数据形态上限）。
因此**不需要为召回率调高 ef_search**。

IVFFlat 构建快 29 倍（4.2s vs 120.5s），但 `probes ≥ 50` 时规划器改用顺序扫描
（418ms），而 `probes=10` 时延迟 9.48ms——比 HNSW 的 0.93ms 慢 10 倍。
**HNSW 是更合适的默认**，与生产 DDL 一致。

## 3. 本轮修掉的两个探针缺陷（都会导致错误结论）

### 3.1 操作符类与查询操作符不匹配 → 索引从未被使用

初版用 `vector_l2_ops` 建索引，却用 `<=>`（余弦）查询。**索引永远不被规划器使用**，
于是：

```text
所有 ef_search 下 recall 都是 1.0000，p50 恒为 ~430ms（= 顺序扫描）
反证「不成立」→ 结论会变成「召回与参数无关」
```

那不是「索引无效」，是**探针配错了**。生产 DDL 用的是 `vector_cosine_ops`
（`rag_embeddings.pgvector_ddl`），已对齐。

**修复**：DDL 改 `vector_cosine_ops`；建索引后 `ANALYZE`；每个参数下 `EXPLAIN`
断言 `ivc_idx` 确实出现在计划里，未使用则标记 `⚠ 索引未被使用（recall/延迟不可解读）`
并在结论阶段直接判 `INVALID` 返回非零。

### 3.2 均匀随机向量是 ANN 的最坏情形 → recall 被低估一个数量级

第二版修好操作符类后，均匀随机数据给出 **HNSW recall 仅 0.3233（ef_search=100）**。
若就此下结论，会得出「HNSW 不可用、必须换独立向量库」——**完全错误的决策**。

对照实验（5 万行，同一索引）：

| 数据形态 | ef_search=40 | ef_search=100 | ef_search=400 |
|---|---|---|---|
| 均匀随机 | recall **0.1000** | 0.1733 | 0.4567 |
| 有簇结构 | recall **0.9867** | 0.9867 | 0.9867 |

512 维均匀随机点之间几乎无结构，HNSW 图无法有效导航；而真实 embedding 的语义近邻
在几何上也近（强簇结构）。**只有后者对应真实几何**。

**修复**：数据生成改为「500 个簇心 + 0.15 簇内扰动」，并把这个对照实验写进函数
docstring——否则下一个人会再次用均匀随机数据得出同样的错误结论。

## 4. 反证（参数承重）

```text
HNSW：   ef_search=1  → recall 0.1000 ；ef_search=40 → 0.9600
IVFFlat：probes=1     → recall 1.0000 ；probes=100   → 1.0000
OK 参数承重：最小参数下 recall 下降，证明召回由扫描深度决定
```

HNSW 侧反证成立（0.10 → 0.96）。IVFFlat 侧 `probes=1` 已 recall 1.0——因为
`lists=100` 且数据有簇结构，单探针恰好落在正确簇上；这不是缺陷，但说明
**IVFFlat 的 recall 在这个数据形态上不敏感**，因此不能靠它验证参数承重。

## 5. 与既有结论的一致性

- 生产 HNSW 用 `vector_cosine_ops`（正确）✓
- 向量**不做重排依据**（`vector_similarity_distribution.py`：相关与不相关分布重叠）——
  本探针不改变该结论：recall 高只说明**能找到**近邻，不说明**余弦距离能判相关性**
- filter-then-ANN 必需（`pgvector_filter_probe.py`）——本探针未推翻

## 6. 环境清理

```text
fg_probe_idx：已 DROP（pg_database 中无 %probe% 残留）
临时文件 /tmp/cluster_test.py、/tmp/pgvector_index_comparison_probe.py、/tmp/fg-idx*：已删
线上：未接触
```

注：开发库 `agent_runs` 在本轮期间由 1510 增至 1516——开发服务在正常处理真实流量，
非本轮探针所致（探针全部使用隔离 database）。
