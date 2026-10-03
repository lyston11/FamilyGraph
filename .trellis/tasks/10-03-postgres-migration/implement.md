# 实施计划：PostgreSQL 主存储与租约协调迁移

## Phase A：盘点与实验

- [ ] 扫描 SQLite-specific SQL、`BEGIN IMMEDIATE`、PRAGMA、FK/CHECK/partial index、Alembic downgrade 和并发 fixture。
- [ ] 启动隔离 PostgreSQL，验证连接池、事务隔离、时间/JSON/枚举/唯一约束映射。
- [ ] 建立 agent/RAG 关键表的 schema prototype，先不接业务流量。

## Phase B：控制层 schema

- [ ] 建立 runs/events/attempts/lease/settle/audit 的 PostgreSQL schema 和索引。
- [ ] 实现 lease `SKIP LOCKED`/CAS、renewal、cancel、settle/recovery 并做并发故障注入。
- [ ] 验证 Assistant/Steward fence、token scope、egress one-audit 和两阶段写回。

## Phase C：数据导入与对账

- [ ] 从隔离 SQLite 快照导出，保留原始 ID/时间/状态/外键关系。
- [ ] 导入并执行行数、摘要、状态、授权 scope、attempt/run、RAG revision/citation 对账。
- [ ] 设计 shadow/dual-read；双写仅限明确的 append-only 路径并记录主写者。
- [ ] 做备份恢复、失败重试、重复导入和校验失败 refusal 演练。

## Phase D：开发灰度

- [ ] 先切 internal control-plane 的开发流量，观察 lease/heartbeat/settle/recovery。
- [ ] 再切 agent execution 与 domain read/write；每阶段有计数、hash、scope 和审计对账。
- [ ] 明确 SQLite 过渡模式上限，禁止在过渡期宣称生产多租户并发已达标。

## Phase E：收尾

- [ ] 运行迁移往返、downgrade/refusal、backend/agent 回归和多租户压力矩阵。
- [ ] 更新部署/备份/恢复/运维文档；线上只生成手动发布和回滚步骤。
- [ ] PostgreSQL 故障、网络分区、重复消息和进程重启时验证不双主、不丢 lease 终态。

## 回滚点

M0/M1 实验失败不接流量；M2 对账失败不切 writer；M3 dual-write 失败停在旧主写者；M4 control-plane 回退需保留 PostgreSQL 已验证数据且不把 SQLite/PG 设为双主。
