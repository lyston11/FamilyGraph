# PostgreSQL 运行运维与切换恢复

## Goal

补足 PostgreSQL schema 迁移之外的生产运行设计：连接池、PgBouncer、备份恢复、PITR、HA、升级、故障切换、开发切换和回滚。

## Requirements

- 按 control/execution/background/admin 规划连接预算，覆盖多进程/多实例总连接数；禁止 overflow 伪装排队。
- 明确 PostgreSQL backup、WAL/PITR、restore rehearsal、RPO/RTO、版本升级和故障切换；线上由用户手动发布。
- PgBouncer 只在 prepared statement、transaction pooling、LISTEN/NOTIFY 和 session state 兼容性验证后采用。
- 迁移切换有 readiness、schema version、writer epoch、refusal guard、对账和回滚；禁止 SQLite/PG 双主。
- 记录数据保留/归档策略，不能无限增长 event/egress/audit/RAG index 表影响多租户性能。

## Dependencies

依赖 postgres-migration 的 schema/事务合同和父任务的 control/resource budget；不实现业务迁移本身，也不操作线上环境。

## 实测证据（2026-10-06）：连接预算已量化

`scripts/migration-proof/pg_connection_budget_probe.py` 在 PostgreSQL 16.15 上实测：

| 项 | 值 |
|---|---|
| `max_connections` | 100 |
| `superuser_reserved_connections` | 3（可用 97） |
| 当前池 | `pool_size=5` + `max_overflow=10` = **单实例 15** |
| `max_locks_per_transaction` | 64 |

**多实例总需求**：

| 实例数 | 总需求 | 占可用 | 结论 |
|---|---|---|---|
| 2 | 30 | 31% | 安全 |
| 4 | 60 | 62% | 安全 |
| 6 | 90 | 93% | **超限** |
| 8 | 120 | 124% | **超限** |

**结论**：当前配置下最多约 **4 个实例**可安全并行（按 70% 留余量）。超出后新实例或
新请求会随机连接失败，且难以归因到配置。证据：
`research/evidence/pg-connection-budget.md`。

### 实测发现（两个待实现项）

1. **超级用户会吃掉保留连接**：探针以超级用户建立到 99 个连接
   （`max_connections=100`），说明 `superuser_reserved_connections` 只对非超级用户生效。
   **生产应用必须使用非超级用户**，否则占用为运维/恢复保留的连接。
2. **`application_name` 为空**：`pg_stat_activity` 无法区分连接归属，多实例/多池场景下
   无法诊断「哪个池占满了连接」。需在 engine 的 `connect_args` 中设置。

## Acceptance Criteria

- 隔离环境完成 backup/restore/PITR/故障重启演练，关键 run/attempt/lease/audit/RAG 合同不丢失。
- 连接上限、池等待、控制面保留容量在多实例总预算内可计算且可观测。
- 开发切换与回滚步骤可执行；校验不通过不会切 writer。
- 归档/分区/保留策略不破坏 `(run_id, seq)`、egress exactly-once、citation 和审计合同。
