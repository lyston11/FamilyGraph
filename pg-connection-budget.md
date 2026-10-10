# PostgreSQL 连接预算与多实例上限

由 `scripts/migration-proof/pg_connection_budget_probe.py` 生成（真实执行）。

- `max_connections = 100`，扣 `superuser_reserved_connections = 3` 后可用 **97**
- 当前池：`pool_size=5` + `max_overflow=10` = 单实例 **15**
- `max_locks_per_transaction = 64`

## 多实例总需求

| 实例数 | 总需求 | 占可用 | 结论 |
|---|---|---|---|
| 1 | 15 | 15% | 安全 |
| 2 | 30 | 31% | 安全 |
| 3 | 45 | 46% | 安全 |
| 4 | 60 | 62% | 安全 |
| 6 | 90 | 93% | **超限** |
| 8 | 120 | 124% | **超限** |

## 结论

单实例 15 连接在 97 可用连接下，**最多约 6 个实例**可安全并行（按 70% 留余量则约 4 个）。
超过后新实例或新请求将随机连接失败，且该故障难以归因到配置。

因此扩容前必须：

1. 显式计算 `实例数 × 单实例池上限 ≤ 可用连接 × 0.7`；
2. 为 control-plane 保留容量（不与执行面共用同一池）；
3. 设置 `application_name`，使 `pg_stat_activity` 可区分连接归属；
4. 考虑 PgBouncer（但需先验证 transaction pooling 与 prepared statement、
   `LISTEN/NOTIFY`、session state 的兼容性）。

## 实测发现

1. **超级用户会吃掉保留连接**：探针用超级用户连接建立了 99 个连接
   （`max_connections=100`），说明 `superuser_reserved_connections=3`
   只对非超级用户生效。**生产应用必须使用非超级用户**，否则会占用
   为运维/恢复保留的连接。
2. **`application_name` 为空**：`pg_stat_activity` 无法区分连接归属，
   多实例/多池场景下无法诊断「哪个池占满了连接」。属待实现项。

## 未覆盖

- **未测 PgBouncer**（transaction pooling 的兼容性验证属实现工作）；
- 未测连接建立延迟对 control-plane 预算的影响；
- 未测真实多实例部署（本探针只算预算与上限行为）。

