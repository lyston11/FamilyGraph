# SQLite 兼容性盘点与 PostgreSQL 原型（2026-10-04）

在隔离的 `postgres:16-alpine` 容器（开发服务器，端口 55432，独立库）上实测。
未接触开发库或线上库。

## 一、源码级盘点（`backend/app`，排除 `__pycache__`）

| 构造 | 处数 | 文件数 | 迁移影响 |
|---|---|---|---|
| `BEGIN IMMEDIATE` | 18 | 12 | **语义必须重做**，见下 |
| `sqlite` 相关 | 46 | 22 | 方言、驱动、备份路径 |
| `json_extract(` | 11 | 4 | → `->` / `->>` 或 `jsonb` |
| `PRAGMA` | 7 | 2 | → 连接参数 / `SET` |
| `fts5` | 5 | 2 | **无对等物**，见下 |
| `import sqlite3` | 5 | 5 | 驱动替换 |
| `strftime(` | 2 | 2 | → `to_char` |
| `MATCH`（FTS 查询） | 1 | 1 | → `to_tsvector`/`pg_trgm` |

`INSERT OR IGNORE` / `INSERT OR REPLACE` / `AUTOINCREMENT` / `WITHOUT ROWID` /
`STRICT` / `GLOB` / `group_concat` / `last_insert_rowid` / `changes()`：
源码中**未发现**直接使用（`ON CONFLICT` 是两者通用语法）。

## 二、`BEGIN IMMEDIATE` 的迁移语义（不是机械替换）

`app/commands/context.py::_begin_immediate` 的现有实现已经**按后端分派**：

```python
if not isinstance(raw, sqlite3.Connection) or raw.in_transaction:
    return
sa_conn.exec_driver_sql("BEGIN IMMEDIATE")
```

非 SQLite 连接是 no-op，注释写明「由该后端自身隔离级别负责」。因此迁移时**不需要**
逐处改写这 18 个调用点，但必须回答一个设计问题：这些 check-then-act 序列在
PostgreSQL 的默认隔离级别（READ COMMITTED）下**不再自动串行**——SQLite 的单写者
提供了免费的串行化，PostgreSQL 没有。

`database-guidelines.md` 已明确记录该判断：「`BEGIN IMMEDIATE` 的真正价值是覆盖跨读取的
多步 check-then-act 窗口」。因此每一处要么有唯一索引/约束兜底，要么需要显式行锁或
advisory lock。**这是本任务的主要工作量，不能靠 no-op 分支蒙混过去。**

## 三、租约语义原型：朴素移植是错的

在真实 PostgreSQL 上并发验证 `steward_assist.lease_attempt` 的同形查询。

### 错误版本（`SKIP LOCKED` + 计数子查询）

```
并发领取: 8 次成功, 唯一 8 个          ← 不重复，正确
每租户 in_flight: [{tenant:1, n:5}, {tenant:2, n:3}]   ← 每租户上限 2 被违反！
```

原因：`SKIP LOCKED` 只防止**同一行**被重复领取，不防止**同一租户**被超额领取。
计数子查询在各自的 READ COMMITTED 快照里读不到其他并发事务尚未提交的 `in_flight`，
于是每个 worker 都看到「本租户只有 0 个在跑」。

**后果**：这正是 E1 执行单元设计所依赖的 per-space 并发上限
（`STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`）。静默失效会让一个空间同时跑
任意多个 attempt，直接击穿该约束。而且它**不会报错**——只是上限不生效。

### 修正版本（租户级 advisory lock）

```sql
SELECT pg_advisory_xact_lock(4242, %(tenant)s);   -- 先按租户串行
WITH picked AS (
  SELECT id FROM lease_probe
   WHERE status='reserved' AND next_attempt_at <= now()
     AND (SELECT count(*) FROM lease_probe x
           WHERE x.tenant = lease_probe.tenant AND x.status='in_flight') < %(cap)s
   ORDER BY next_attempt_at, id
   FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE lease_probe p SET status='in_flight', ... FROM picked WHERE p.id = picked.id
RETURNING p.id, p.tenant;
```

实测：

```
唯一领取 4 个；每租户 in_flight: [{tenant:1, n:2}, {tenant:2, n:2}]   ← 上限生效
不同租户并发耗时 0.009s    ← advisory lock 按租户分键，跨租户不阻塞
```

同一租户的领取串行（计数看得见前一个已提交的 `in_flight`），不同租户用不同 lock key
仍完全并发。**这是租约迁移的正确形态**，而不是「把 `BEGIN IMMEDIATE` 换成
`SKIP LOCKED`」。

### 仍需验证的语义

- 租约**续期**与**回收**在 advisory lock 下的行为（本次只验证了领取）。
- `pg_advisory_xact_lock` 的 key 空间分配（当前原型用 `(4242, tenant)`，需登记避免碰撞）。
- 长事务持锁对 control-plane 的影响：advisory lock 必须在**短事务**内释放。

## 四、RAG 检索：FTS5 无对等物（阻塞项）

`rag_chunks_fts` 是 SQLite **FTS5 虚拟表**，`tokenize='trigram'`：

```sql
CREATE VIRTUAL TABLE rag_chunks_fts USING fts5(
  chunk_id UNINDEXED, text, tokenize='trigram')
```

查询用 `bm25(rag_chunks_fts) AS rank` + `WHERE rag_chunks_fts MATCH :match`
（`app/services/memory_rag.py:1275-1279`）。

PostgreSQL **没有 FTS5**，也没有 `MATCH`/`bm25()`。候选替代：

| 方案 | 与现有语义的差距 |
|---|---|
| `pg_trgm` + GIN | 提供相似度/`LIKE` 加速，但**没有** `MATCH` 布尔查询语法，也没有 `bm25` 排序 |
| `tsvector`/`to_tsquery` | 是词级全文检索，**不是 trigram**；CJK 需要额外分词器（`zhparser`/`pgroonga`），否则中文按空白切分基本不可用 |
| `pgroonga` | 语义最接近（支持 CJK、有评分），但是**额外扩展**，运维面变大 |

选 `tokenize='trigram'` 的原因是为 CJK 与短文本服务（`memory_rag.py:1048` 注释）。
因此**不能**简单换成 `tsvector`——那会静默降低中文检索质量。

`_FALLBACK_SCAN_LIMIT` 的 `LIKE ... ESCAPE '!'` 后备路径在 PostgreSQL 上语义等价，
但它只是后备，不是主路径。

**结论**：RAG 检索需要一个**独立的检索方案决策**（pg_trgm vs pgroonga vs 分词器），
并需要一套检索质量对照基准。这不是迁移的机械部分，应作为独立子任务，不能夹在
schema 迁移里悄悄替换。

## 五、备份/恢复路径

`app/backup.py` 用 `sqlite3` 驱动 + `strftime` 直接操作文件。PostgreSQL 需要换成
`pg_dump`/`pg_restore`（或逻辑导出），并重新验证：一致性快照、WAL/复制语义、
恢复演练、以及「不得直接复制运行中主库」这条既有规则在 PostgreSQL 下的对应形式
（`pg_dump` 本身是一致的，但仍需在隔离环境验证）。
