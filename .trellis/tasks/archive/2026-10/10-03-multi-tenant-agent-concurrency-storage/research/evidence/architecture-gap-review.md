# 多租户架构缺口审查（2026-10-04）

## 结论

PostgreSQL 迁移和 FTS 选型不是全部问题。当前系统还有五个独立故障域，不能塞进 schema migration：control-plane starvation、sidecar shared process、provider long-stream amplification、数据库运维恢复、最终容量证据。

## 证据与设计决定

### 1. Control-plane starvation

已有 SQLite/AnyIO 现场证明，工具突发和连接池等待可以让 heartbeat/lease/health 一起延迟。单纯增加 semaphore 只保护某一入口；必须分 control/execution/background worker 和 DB reserve。最终需要在 PostgreSQL 多实例下验证，而不是只在单进程 SQLite 中验证。

### 2. Sidecar shared fault domain

Assistant/Steward 可配置在同一 Node 进程，共享 event loop、HTTP client、heap、retry storm 和 shutdown。逻辑 slot 隔离不能保证进程故障隔离。短期做 kind-specific slots/pools，长期由并发矩阵决定是否拆进程；无论是否拆进程，internal token/fence/settle/recovery 合同不变。

### 3. Provider long stream

RunRetryBudget 只限制真实请求尝试，不能限制已建立长流、backpressure、连接持有和同一 upstream 的 stream storm。需要独立 stream-level quota、wall-clock deadline、upstream/kind/tenant circuit，且保持 `sent`、egress exactly-one 和 unknown 计费语义。

### 4. PostgreSQL 运维

schema 正确不等于可运行：每实例连接池总和、PgBouncer 的 session/transaction pooling 语义、WAL/PITR、restore 后 lease recovery、writer epoch、长事务和 append-only 表增长都会决定系统是否真正支持多租户。独立任务负责这些问题。

### 5. RAG 词法与语义

FTS5 trigram、PGroonga、pg_trgm/tsvector 和 pgvector 不是等价物。PGroonga-first 只是一项可验证候选；必须有不含真实个人数据的 golden corpus。授权、scope、revision 和 citation 先于任意 lexical/vector 候选，索引只能是可重建派生物。

### 6. 容量验收

单 run 成功和单元测试无法证明隔离。必须测 account×space×kind、同用户跨空间、同空间多用户、长流、tool burst、RAG/index、provider/DERP、数据库、Redis、sidecar 和租约恢复，并同时看 p95/p99、拒绝率、饥饿、重复 lease/settle 和用户可见终态。

## 新子任务

- `10-04-control-plane-fault-domain`
- `10-04-provider-reliability-boundaries`
- `10-04-postgres-operations-cutover`
- `10-04-lexical-search-migration`
- `10-04-multitenant-load-acceptance`

它们的 PRD/design/implement 和 context manifests 已建立并通过 `task.py validate`。没有任务被启动或部署；线上未操作。
