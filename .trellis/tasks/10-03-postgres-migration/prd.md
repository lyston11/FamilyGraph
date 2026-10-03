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
