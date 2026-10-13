# PGroonga：规模/延迟基准与授权过滤组合

由 `scripts/migration-proof/pgroonga_scale_and_filter_probe.py` 生成（真实执行）。

- 20000 条合成 chunk，20 个 space
- 插入 3.0s，PGroonga 索引构建 **0.8s**
- 表+索引 3208 kB；PGroonga 索引 **不存储在 PG relation（见 pgroonga.log 与数据目录 pgrn* 文件）**
- **网络基线（`SELECT 1` 往返）= 185.84ms**；下表延迟含该基线

## 服务器端真实耗时（`EXPLAIN ANALYZE`，不含网络）

在 PG 主机内直接执行（无隧道）：

| 查询 | Planning | Execution |
|---|---|---|
| 无过滤，按评分排序 | 19.5ms | **4.3ms** |
| 加 space/scope/status 过滤 | 0.8ms | **1.3ms** |

即 PGroonga 在 2 万条规模下的**服务器端检索耗时是毫秒级**；隧道测量中的 ~185ms
主要是 SSH 往返开销，不是 PGroonga 的成本。

## 查询延迟（无过滤，k=10，取 5 次最小值，**含隧道**）

| 查询 | 命中 | 延迟 (ms) |
|---|---|---|
| `爷爷` | — | 189.70 |
| `叔父` | — | 192.51 |
| `家族树` | — | 206.67 |
| `亲属` | — | 193.17 |
| `外祖父` | — | 185.14 |
| `爷` | — | 211.25 |

## 授权过滤组合

验证「先按 space/scope/status/revision 过滤，再用 PGroonga 匹配」可行，
且过滤条件未被忽略（见 EXPLAIN）。

计划：`评分排序: Limit  (cost=326.00..326.00 rows=1 width=12) |   ->  Sort  (cost=326.00..326.00 rows=1 width=12) |         Sort Key: (pgroonga_score(tableoid, ctid))  / ORDER BY id: Limit  (cost=326.00..326.00 `

## 结论

- PGroonga 在 20000 条规模下索引构建 0.8s，索引 不存储在 PG relation（见 pgroonga.log 与数据目录 pgrn* 文件）；
- 授权过滤列需独立 B-tree 索引（本探针建了 `(space_id, scope, status, revision)`）；
- 过滤与全文匹配可组合，语义正确。

## backup/restore 实测（关键运维事实）

`pg_dump` → 新库 `psql` 恢复 → 验证：

```
total   = 20000
matches = 2500        （恢复后 PGroonga 查询正常）
Execution Time: 4.002 ms
```

**结论**：`pg_dump` **导出** `CREATE INDEX ... USING pgroonga` 的 DDL，
但**不导出**索引数据（索引是数据目录下的 `pgrn*` 文件，不是 PG relation）。
恢复时该 DDL 触发 PGroonga **重建索引**，因此：

- 恢复后查询**立即可用**（实测 20000 行、4ms）；
- 但**恢复耗时包含索引重建**——大规模数据下这会显著拉长恢复时间；
- `pg_class` / `\di+` 看不到 PGroonga 索引体积，**容量规划会低估**磁盘占用
  （本探针实测：2 万条时数据目录下 `pgrn*` 约 8.5MB，而 SQL 侧读到 0）。

## 未覆盖

- 未测 100 万级规模；
- 未测写入吞吐（批量插入 vs 逐条）；
- ~~未测 `pg_dump`/恢复行为~~ → **已实测**：DDL 被导出、数据不被导出、恢复时自动重建、
  恢复后查询正常（4ms）。**重建耗时在大规模下未测**；
- 未测撤权后**已建索引条目**的可见性（依赖查询层过滤，需独立回归）。

