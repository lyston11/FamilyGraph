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

1. **pgvector 语义路径未接入**：`10-03-pgvector-rag` 的 filter-then-ANN 已实测，
   但尚未接入 `search_rag` 的 union/rerank。
2. **索引版本切换回归**：`index_version` 切换与「查询必须带过滤」的 mutation 回归未做。
3. **PG 上的 `_rows_to_hits` 语义**：该函数仍假设 FTS5 的 `rank` 语义（`bm25` 越小越好），
   而 PGroonga 的 `pgroonga_score` 越大越好。分派器已用 `ORDER BY rank DESC` 处理排序，
   但 `rank_by_order` 与后续 rerank 的组合未在真实 PG 上端到端验证。
4. **RAG 关闭/来源失效/版本冲突的 pgvector 路径语义**未验证。

## 证据等级

方言分派与授权过滤：**L1**（单测 + 真实 PG 执行 + 反证）。
端到端检索语义（union/rerank、revision 切换）：未验证。
