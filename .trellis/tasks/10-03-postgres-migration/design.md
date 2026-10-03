# 技术设计：PostgreSQL 主存储与租约协调迁移

## 1. 目标

PostgreSQL 成为唯一持久事实、事务和租约协调真源；SQLite 仅作为可验证迁移过渡。迁移不得创建双主事实，必须保留 run/attempt/fence/settle/egress/RAG 合同。

## 2. Schema 分层

- 领域层：账户、空间、成员、关系、授权和 projection。
- Agent 控制层：jobs（仅 Assistant）、runs、events、attempts、lease/renewal、settle/recovery、tool execution。
- 审计层：provider egress、tool/audit、资源诊断；敏感正文不入诊断。
- RAG 层：memory source、document/chunk、revision、citation/context build，未来 embedding 以外键绑定。

高频表按访问路径建立复合索引；append/audit 根据基准评估时间分区或归档，但不能破坏每次真实 egress 恰好一条审计和 `(run_id, seq)` 幂等。

## 3. 事务与并发

将 SQLite `BEGIN IMMEDIATE` 分解为真正需要的语义：

- 单条件 CAS 用 `UPDATE ... WHERE state/version/lease_owner` 并检查 affected rows；
- 多步 check-then-act 使用短事务和行锁；
- lease 选择使用 `FOR UPDATE SKIP LOCKED` 或等价持久 ready queue，按 tenant/fairness 过滤；
- lease renewal、cancel、settle、recovery 各自为短事务；
- model/network I/O 绝不在事务内；
- 唯一约束、FK、CHECK、幂等和 `ON CONFLICT` 在 DB 层保留。

连接池必须按 control/execution/background 设计或至少保留 control 连接；记录 checkout/transaction wait 与持有时间，不能用 overflow 掩盖排队。

## 4. 迁移形态

```text
M0 inventory + isolated PostgreSQL
M1 schema and constraints
M2 import + row/count/hash/scope verification
M3 shadow read / dual-read
M4 selected append-only dual-write
M5 control-plane writer cutover
M6 domain writer cutover and SQLite retirement
```

每一步先执行 refusal guards，再做 DDL/version move；切换前完成备份和恢复演练。双写必须有明确主写者、幂等 key、失败处理和对账，不允许两边独立裁决 lease/settle。校验失败停留在旧阶段，不自动修数据。

## 5. 恢复与部署

PostgreSQL 故障时不新增 lease；已有 lease 由持久 recovery 收口。导入使用隔离 SQLite 快照，不复制正在运行主库。开发 systemd 与线上 compose 完全分离，线上只由用户手动发布。回滚只回路由/读写阶段，不删除已验证导入数据。

## 6. 兼容性

internal API、token claims、Assistant/Steward kind、sidecar 无 DB、provider gateway、egress/fence/settle、RAG scope/revision/citation 保持版本兼容。需要新增的 capacity/queue/retry/migration health 字段采用 additive schema 并有双侧测试。
