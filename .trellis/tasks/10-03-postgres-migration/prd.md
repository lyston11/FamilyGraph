# PostgreSQL 主存储与租约协调迁移

## Goal

将 SQLite 单写者主路径迁移到 PostgreSQL，支持多用户并发读写、可靠 lease/CAS、短事务和可恢复的 run/attempt/settle 协调，同时保留现有授权、egress、fence、RAG 和审计合同。

## Scope

- 盘点 SQLite-specific SQL、`BEGIN IMMEDIATE`、迁移约束、索引、FK、CHECK、触发器和 downgrade。
- 设计 PostgreSQL schema、连接池、行锁、`FOR UPDATE SKIP LOCKED`/持久队列、CAS、幂等和 lease recovery。
- 设计隔离快照、导出/导入验证、shadow/dual-read/受控 dual-write、切 writer 和回滚。
- 在隔离 PostgreSQL 完成并发、迁移往返、备份恢复和数据一致性实验。

## Dependencies / non-dependencies

- 依赖父任务接受的 PostgreSQL-as-source-of-truth 基线，以及 agent resource key/control-plane 合同。
- 不依赖 Redis 或独立向量数据库；Redis 不得成为迁移期间的第二事实来源。
- 运行时资源隔离子任务可先用 SQLite 建模，但本子任务必须为其提供 PostgreSQL 事务/连接池边界。
- 线上数据库、线上 compose 和线上 env 不在本子任务自动操作范围。

## Acceptance Criteria

- PostgreSQL 在隔离环境通过 schema/约束/索引/迁移往返和并发租约测试。
- agent run/attempt lease、heartbeat、settle、recovery、egress audit、fence 和 RAG source/revision 合同保持可观察等价。
- SQLite 历史数据导入有行数、摘要、授权 scope、状态和审计对账；校验失败不切 writer。
- 有明确开发灰度、备份、回滚、双主禁止和线上手动发布步骤。
- 明确 SQLite 退出主协调路径的阶段和过渡期并发上限。


## Execution reset (2026-10-05)

当前任务在执行阶段连续暴露出「本地测试通过、换 PostgreSQL 才失败」的缺陷：partial index 方言谓词丢失、JSON 约束/查询方言不兼容、`BEGIN IMMEDIATE` 被行锁替代后出现反向锁序死锁。问题根因不是单个实现粗心，而是缺少执行前证明门。

因此在 `10-05-migration-proof-gates` 完成前，本任务只允许做证据、原型和设计更新，不允许继续扩大业务代码实现或切 writer。该子任务是本任务的硬前置，完成条件见其 `prd.md` / `design.md` / `implement.md`。

## Cross-task boundaries (2026-10-04)

本任务只负责 PostgreSQL 作为持久真源的 schema、事务、lease/CAS、导入对账和 writer 迁移。以下问题已拆到独立子任务，不再隐含在本任务中：

- `10-04-control-plane-fault-domain`：backend/sidecar control-plane worker、DB reserve、故障域和多实例 recovery；
- `10-04-provider-reliability-boundaries`：长流容量、backpressure、连接生命周期和 circuit breaker；
- `10-04-postgres-operations-cutover`：连接预算、PgBouncer、WAL/PITR、HA、归档、writer epoch 和发布恢复；
- `10-04-lexical-search-migration`：FTS5 trigram 的 PGroonga/Unicode n-gram 质量迁移；
- `10-04-multitenant-load-acceptance`：跨子任务的真实并发与故障注入验收。

本任务必须为这些子任务提供 PostgreSQL transaction/capacity/health 接缝，但不把它们未完成的结果伪装成 PostgreSQL migration 已完成。
