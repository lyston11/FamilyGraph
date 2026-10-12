# C6：词法检索的方言分派（PGroonga 接入）

## 交付

`app/services/rag_search_provider.py`：把「怎么找词法候选」按方言分派。

| 方言 | 主路径 | 短词后备 |
|---|---|---|
| SQLite | FTS5 `MATCH` + `bm25()` | 参数化 `LIKE`（trigram 无法匹配 <3 字符） |
| PostgreSQL | PGroonga `&@~` + `pgroonga_score` | **不需要**：两字中文词正常匹配（实测），合并进主查询 |

`memory_rag.search_rag` 改为调用该分派器，不再直接写 FTS5 SQL。

## 授权边界（本层最重要的一点）

**授权过滤不在 provider 层**。`eligibility` 谓词由调用方传入并原样拼进两种方言的
SQL。理由：检索索引不承载授权——实测撤权只改主表状态，索引条目仍在（PGroonga 与
pgvector 都确认过）。可见性**完全**依赖查询层过滤。

因此 provider 只决定「怎么找候选」，绝不决定「谁能看」。任何在这里放宽或跳过
`eligibility` 的改动都是授权漏洞。

## 实测（真实 PGroonga + 真实 PostgreSQL）

`scripts/migration-proof/pgroonga_branch_probe.py`：

```
PGroonga 索引已建立: ix_rag_chunks_pgroonga
带过滤命中 1 行（期望 1：撤权文档被过滤）
反证（无过滤）命中 2 行（期望 2：撤权文档可见）
OK  过滤条件承重（去掉它就能查到撤权内容）
PASS
```

**反证是关键**：只测「带过滤命中 1 行」不能证明过滤承重——可能只是检索本身没命中。
必须证明「去掉过滤能查到撤权内容」，才说明可见性依赖该谓词。

## PGroonga 索引必须在 baseline 显式创建

`PGROONGA_INDEX_DDL` 由 provider 导出，因为该索引**不在 ORM 元数据里**（扩展索引），
`create_all` 看不到它——与 69 个触发器同类问题。若 baseline 漏建，PostgreSQL 上
检索会静默退化为顺序扫描（功能对、性能崩）。

## 本次踩到的缺陷

**我误判了 `eligibility` 的形态**：以为它带前导 `AND`，实际 `_ELIGIBILITY_SQL` 是
**裸谓词**，由模板拼成 `WHERE <condition> AND <eligibility>`。探针第一次执行时拼出
`AND AND` → 语法错误。

这个缺陷同时暴露了单测的弱点：子串断言（"SQL 里有 &@~"）**不能**发现语法错误。
因此补了 `pgroonga_branch_probe.py`——「把生成的 SQL 真的执行一次」。

## 未完成（诚实声明）

1. **pgvector 语义路径**：**机制已交付**（embedding 抽象、filter-then-ANN SQL、
   RRF 融合），但**未接入 `search_rag`**，且**没有真实 embedding provider**
   （代码库此前完全没有 embedding 能力）。接入需要：选定 provider → 补
   `resolve_embedder` 实现 → 建 `rag_chunk_embeddings` → 在 `search_rag` 里
   union/rerank。
2. **索引版本切换回归**：`index_version` 切换与「查询必须带过滤」的 mutation 回归未做。
3. **PG 上的 `_rows_to_hits` 语义**：该函数仍假设 FTS5 的 `rank` 语义（`bm25` 越小越好），
   而 PGroonga 的 `pgroonga_score` 越大越好。分派器已用 `ORDER BY rank DESC` 处理排序，
   但 `rank_by_order` 与后续 rerank 的组合未在真实 PG 上端到端验证。
4. **RAG 关闭/来源失效/版本冲突的 pgvector 路径语义**未验证。

## 证据等级

方言分派与授权过滤：**L1**（单测 + 真实 PG 执行 + 反证）。
端到端检索语义（union/rerank、revision 切换）：未验证。


## 接入真实 schema（后续更新）

`rag_schema_e2e_probe.py` 在**真实 88 表 schema** 上端到端验证：

```
真实 schema 带过滤命中 [9201]（期望 [9201]）
反证（无过滤）命中 [9201, 9202]（期望含 9202）
OK  授权过滤承重（去掉它就能查到撤权内容）
PASS
```

`pg_baseline_build.py` 现在会显式建立 PGroonga 扩展索引并断言其存在：

```
[OK ] PGroonga 扩展索引已建立
[OK ] ORM metadata create_all 成功
表=88 索引=255 CHECK=118 FK=212 UNIQUE=32 局部索引=16 触发器=0
```

### 为什么真实 schema 探针与最小表探针都要保留

两者的失败含义不同：

- `pgroonga_branch_probe.py`（2 张最小表）失败 → provider 写错了 SQL；
- `rag_schema_e2e_probe.py`（真实 88 表）失败 → 真实 schema 上有 provider 没考虑到的约束。

**实测正是后者**：最小表探针通过，真实 schema 探针连续撞上三个 NOT NULL 列
（`embedding_status`、`created_at`、`updated_at`）。这说明「SQL 形状对」不等于
「能在真实表上跑」。


## 语义检索机制（后续更新）

`app/services/rag_embeddings.py` 交付三件事：

### 1. embedding provider 抽象

```python
EmbeddingConfig(provider, model, dimension)
load_config()          # 从环境读；未配置时 configured=False（fail-closed，不报错）
resolve_embedder(cfg)  # 未配置/未实现 → EmbeddingUnavailable
```

**未配置时 fail-closed**：不伪造命中、不静默退化为「无结果」（那会让用户以为确实
没有资料），也不回落到 `DeterministicEmbedder`。

`DeterministicEmbedder` 只用于**测试与开发**：它用 token 哈希投影，**没有语义
相似性**——"叔叔" 与 "伯父" 不会被判为相近。把它当生产实现会让语义检索退化成比
词法更差的字面检索，却让系统看起来「已启用向量检索」。因此 `resolve_embedder`
**不会**返回它（已用变异验证：让未配置回落到它会失败）。

### 2. filter-then-ANN 而不是 post-filter

```sql
WITH authorized AS (          -- ① 先按授权过滤
  SELECT ... WHERE <eligibility>
)
SELECT ... FROM authorized    -- ② 再在授权集合上 ANN
ORDER BY embedding <=> :q LIMIT :limit
```

**为什么不能反过来**：实测 post-filter 在低选择性下**静默返回不足 k**（允许 1/10
空间时只剩 1 条，应为 10），而 RAG **无法区分**「无相关内容」与「被授权过滤掉」——
静默缺失会表现为「检索不到」，无人能发现。

代价是 ANN 索引只作用于过滤后集合（可能退化），因此用 `over_fetch`（默认 8×）
再在 union/rerank 阶段裁到 k。**宁可多取再裁，也不要可能静默少返回**。

### 3. RRF 融合（确定性）

```text
score(d) = Σ 1 / (60 + rank_i(d))
```

**为什么不用分数加权**：词法 `bm25`（越小越好）与向量余弦距离（越小越好）
**不同量纲**，直接比较会随数据分布漂移且不可复现。RRF 只用**名次**。

同分时按 `chunk_id` 升序兜底——否则相对顺序取决于字典插入顺序，同一查询两次可能
给出不同顺序，引用列表随之漂移。

### 变异验证（3 组）

| 变异 | 结果 |
|---|---|
| 改成 post-filter（先排序再过滤） | 结构断言失败 |
| RRF 同分时去掉 chunk_id 兜底 | 确定性用例失败 |
| 未配置时返回 DeterministicEmbedder | fail-closed 用例失败 |
