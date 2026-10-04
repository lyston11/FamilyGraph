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

## Acceptance Criteria

- 隔离环境完成 backup/restore/PITR/故障重启演练，关键 run/attempt/lease/audit/RAG 合同不丢失。
- 连接上限、池等待、控制面保留容量在多实例总预算内可计算且可观测。
- 开发切换与回滚步骤可执行；校验不通过不会切 writer。
- 归档/分区/保留策略不破坏 `(run_id, seq)`、egress exactly-once、citation 和审计合同。
