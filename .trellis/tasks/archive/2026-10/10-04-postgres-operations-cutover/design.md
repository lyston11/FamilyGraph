# 技术设计：PostgreSQL 运行运维与切换恢复

## 1. 运行拓扑

```text
control API pool  → PostgreSQL control connections
agent/model pool  → PostgreSQL execution connections
background pool   → low-priority RAG/maintenance connections
admin/read pool   → bounded read connections
```

每个进程的 pool 上限必须乘以实例数后小于 PostgreSQL `max_connections`，并为 migrations、superuser、监控和故障处理保留容量。所有 pool 记录 checkout wait、transaction age、持有时长和超时。

## 2. 备份与恢复

使用 PostgreSQL base backup + WAL archive；开发环境先做 restore rehearsal。定义 RPO/RTO 和恢复后校验：schema version、row counts、FK、run/attempt 状态、未完成 lease、egress/audit 计数、RAG revision/citation。恢复后由 recovery tick 重新裁决过期 lease，不恢复已发送网络请求。

## 3. 切换

开发阶段采用冻结写入的静态快照导入和短停机切换。writer epoch 记录切换世代；旧实例在失去 writer lease 后 fail closed。回滚只切回尚未接受新写入的旧阶段，不把两个数据库重新设为双主。

## 4. 数据生命周期

append-only events/egress/audit 与 RAG index 采用按时间/版本归档或分区的方案，先验证查询和引用回放；业务事实和审计保留期不可由性能清理策略静默删除。
