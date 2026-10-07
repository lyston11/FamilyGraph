# C7：PgBouncer transaction pooling 兼容性

## 实测结论

```
1) 直连对照（基线）            leased=6 per_tenant={1:2, 2:2, 3:2} over=[] mismatch=[]
2) 经 PgBouncer（transaction）  leased=6 per_tenant={1:2, 2:2, 3:2} over=[] mismatch=[]
3) prepared statements         直连 ok=True；PgBouncer ok=True
3b) 强制 prepare               错误：无
4) 会话级 vs 事务级状态          transaction SET LOCAL 可用  advisory_xact_lock 可用
PASS
```

**配额在 transaction pooling 下与直连逐项一致**（每租户 2、counter 无漂移、无错误），
证明「counter + 固定锁序」的形态不依赖会话级连接状态。

## 逐特性核对

| 特性 | transaction pooling | 本仓库现状 | 判定 |
|---|---|---|---|
| `FOR UPDATE`（事务级行锁） | 安全 | C2 配额与租约都用它 | 安全 |
| `SET LOCAL` | 安全 | 无使用 | 安全 |
| 会话级 `SET` | **不可靠** | grep 确认无使用 | 安全 |
| `pg_advisory_xact_lock` | 安全 | 探针验证可用 | 安全 |
| 会话级 `pg_advisory_lock` | **失效** | grep 确认无使用 | 安全 |
| prepared statements | **配置决定** | psycopg3 默认阈值 5 | **必须二选一** |

## 最关键的发现：prepared statement 的失败模式

`test_prepared_statements` 通过**不能**说明没问题——它只说明「在当前配置下没问题」。
若失败模式根本触发不到，PASS 就没有判别力。因此补了**负向**测试（`prepare_threshold=0`
强制立即 prepare）：

| PgBouncer 配置 | `prepare_threshold=0` |
|---|---|
| 默认（支持 prepared） | 通过 |
| `max_prepared_statements=0` | **`DuplicatePreparedStatement: prepared statement "_pg3_0" already exists`** |

**部署约束（二选一）**：

1. PgBouncer 保持默认（支持 prepared statements）——当前验证的形态；
2. 或 psycopg 连接设 `prepare_threshold=None` 关闭自动 prepare。

两者都没有时会在高并发下报 `DuplicatePreparedStatement`，而**本地直连 PostgreSQL
永远测不出**——这是必须写进部署文档的约束。

## 会话级 SET 的「恰好可靠」不得依赖

本次探针观测到 `session_set_reliable: True`，但那只是**连接恰好没被换出**。
transaction pooling 下同一客户端的下一个事务可能落到不同后端连接，因此该结果
不可依赖。探针显式打印这一注记，避免有人据此认为会话级 SET 可用。

## 证据等级

**L3**：真实 PgBouncer 1.26.0、真实 transaction pooling、真实并发竞争（12 线程 /
3 租户 / 上限 2）、含**负向**失败模式验证。含直连对照，因此「一致」是相对结论
而非绝对断言。

## 仍未覆盖

- `max_client_conn` / `default_pool_size` 与后端连接预算的匹配（需真实负载）；
- PgBouncer 重启/故障时的连接恢复行为；
- 多 PgBouncer 实例与 failover。
