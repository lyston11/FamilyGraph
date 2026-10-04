# 实施计划：PostgreSQL 运行运维与切换恢复

## Phase A：容量与连接

- [ ] 计算每个 backend/worker/sidecar 实例的 control/execution/background/admin 连接预算。
- [ ] 在隔离 PostgreSQL 注入 pool exhaustion、长事务、断线、重启和 failover。
- [ ] 决定是否需要 PgBouncer，并验证 session state/prepare/notify 兼容性。

## Phase B：备份恢复

- [ ] 配置隔离 base backup/WAL/PITR 实验。
- [ ] 设计 restore 后 lease recovery、writer epoch 和 readiness 检查。
- [ ] 验证 run/attempt/egress/RAG/citation 对账。

## Phase C：切换与保留

- [ ] 写开发环境冻结写入、静态导入、对账、切换、回滚 runbook。
- [ ] 设计 event/egress/audit/RAG index 的归档或分区，不破坏回放/审计。
- [ ] 故障注入后完成多用户并发和恢复矩阵。

## Gate

- [ ] RPO/RTO、最大连接数、writer epoch、备份恢复证据全部落盘；未通过不进入线上发布。
