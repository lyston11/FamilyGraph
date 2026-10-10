# PGroonga：规模/延迟基准与授权过滤组合

由 `scripts/migration-proof/pgroonga_scale_and_filter_probe.py` 生成（真实执行）。

- 20000 条合成 chunk，20 个 space
- 插入 0.6s，PGroonga 索引构建 **0.3s**
- 表+索引 3208 kB；PGroonga 索引 **不存储在 PG relation（见 pgroonga.log 与数据目录 pgrn* 文件）**
- **网络基线（`SELECT 1` 往返）= 0.09ms**；下表延迟含该基线

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
| `爷爷` | — | 1.92 |
| `叔父` | — | 1.69 |
| `家族树` | — | 2.73 |
| `亲属` | — | 2.61 |
| `外祖父` | — | 1.72 |
| `爷` | — | 1.62 |

## 授权过滤组合

验证「先按 space/scope/status/revision 过滤，再用 PGroonga 匹配」可行，
且过滤条件未被忽略（见 EXPLAIN）。

计划：`评分排序: Limit  (cost=51.57..51.57 rows=1 width=12) |   ->  Sort  (cost=51.57..51.57 rows=1 width=12) |         Sort Key: (pgroonga_score(tableoid, ctid)) DESC / ORDER BY id: Limit  (cost=51.57..51.57 ro`

## 结论

- PGroonga 在 20000 条规模下索引构建 0.3s，索引 不存储在 PG relation（见 pgroonga.log 与数据目录 pgrn* 文件）；
- 授权过滤列需独立 B-tree 索引（本探针建了 `(space_id, scope, status, revision)`）；
- 过滤与全文匹配可组合，语义正确。

## 未覆盖

- 未测 100 万级规模；
- 未测写入吞吐（批量插入 vs 逐条）；
- **未测** `pg_dump`/恢复时 PGroonga 索引的重建行为——但已确认它**不存储于**
  PG relation（`pgrn*` 文件在数据目录），因此 `pg_dump` 不会导出它，
  恢复后必须重建；这一点的**重建耗时**未测；
- 未测撤权后**已建索引条目**的可见性（依赖查询层过滤，需独立回归）。

