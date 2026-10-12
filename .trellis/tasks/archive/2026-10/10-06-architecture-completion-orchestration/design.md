# 技术设计：多租户架构全量完成与连续执行编排

## 1. Architecture decision

### 持久层

```text
PostgreSQL = durable source of truth
Redis      = ephemeral admission/cache/wakeup acceleration
PGroonga   = lexical derived index
pgvector   = semantic derived index
SQLite     = static snapshot / temporary rollback material only
```

任何 lease、counter、attempt、settle、audit、scope、revision、citation 和 recovery 终态必须由 PostgreSQL 裁决。派生索引不得成为授权源或业务状态源。

### 运行时资源层

```text
control-plane pool
  heartbeat / lease / cancel / settle / recovery / health

assistant pool
  account-scoped run/provider/tool

steward pool
  space-scoped run/provider/tool

background pool
  RAG index / lexical maintenance / reconciliation / backup jobs
```

每个 pool 有独立的并发预算、DB connection reserve、retry budget、deadline 和健康指标。任何 pool 不能借用 control reserve。

## 2. PostgreSQL transaction contracts

冻结锁序：

```text
global capacity → kind capacity → tenant capacity → parent/resource → run → attempt/event
```

### Lease

```text
BEGIN
lock global/kind/tenant counter in canonical order
check capacity under lock
select candidate FOR UPDATE SKIP LOCKED
increment counter and write lease/grant generation
COMMIT
```

不把网络、Provider、模型流放在事务内。

### Settle/release

settle/recovery/cancel 必须按 grant/lease generation 幂等。若调用方已取得 run lock，counter release 必须在 fence 前完成，或拆成独立 release transaction；禁止 `run → counter`。

### CAS

单行状态转换使用：

```sql
UPDATE ...
SET status = :next, version = version + 1
WHERE id = :id
  AND status = :expected
  AND version = :version
```

affected rows 为 0 时重新读取并按终态优先级裁决，不用先前 SELECT 结果继续写。

## 3. Baseline migration

不重放 SQLite 历史 Alembic。建立显式 PostgreSQL baseline，包含：

- 表/列/类型/默认值；
- FK/ON DELETE/CHECK/partial unique；
- 69 个 SQLite trigger 的逐项 PostgreSQL 等价物；
- agent control/counter/audit；
- RAG source/document/chunk/revision/citation 元数据；
- lexical/vector index metadata；
- sequence/identity 初始化；
- refusal guards。

导入流程：

```text
static snapshot provenance
→ staging
→ preserve IDs/timestamps/revisions/FKs
→ sequence repair
→ row/hash/scope/status/run-attempt/lease/egress/RAG reconciliation
→ backup/restore
→ refusal on mismatch
```

## 4. Control-plane fault domain

control-plane 是唯一可借用系统保留容量的 pool。Provider 长流、工具 burst、RAG maintenance 和 Redis 延迟必须在 control admission 之外排队或拒绝。

恢复：

```text
lease_until expiry
→ recovery CAS
→ grant/counter release exactly once
→ terminal/requeue according to cancel/revoke/attempt policy
```

sidecar 重启不依赖进程内状态；worker identity、lease generation、writer epoch 都必须持久化或可重新获取。

## 5. Provider contract

Provider egress 前：

```text
run scope/auth
→ provider/kind/account|space quota
→ retry budget
→ circuit state
→ network I/O
```

必须有：connect/header/stream deadline、backpressure、cancel/lease-lost interruption、upstream error classification、egress exactly-once audit。永久 4xx 不转可重试 5xx；run budget exhaustion 使用不可重试 sentinel。

## 6. Redis contract

Redis key 只保存短期 admission/token/cache/wakeup 状态，所有 key 带 tenant/kind/epoch 作用域和 TTL。Redis 故障时：

- control-plane 走 PostgreSQL；
- 普通 admission 使用有界 PostgreSQL fallback 或 fail-closed；
- 不得因为 Redis unavailable 自动放行；
- Redis 恢复不得重放持久 settle 或重新创建 lease。

## 7. Retrieval contract

查询顺序固定：

```text
authorized PostgreSQL candidate scope/status/revision filter
→ PGroonga lexical / pgvector semantic candidate
→ union + deterministic rerank
→ final visibility/citation/revision validation
```

索引内容撤权后可以保留，但任何读取出口必须零命中受限来源。index version、rebuild、failure、rollback 都由维护 lease 和 PostgreSQL metadata 管理。

## 8. Operations and cutover

operations 子任务负责：连接总预算、PgBouncer、WAL/archive/PITR、HA、backup/restore、writer epoch、readiness 和 rollback。

切换：

```text
snapshot-only
→ shadow read
→ control writer
→ assistant/steward writer
→ domain writer
```

失败只回退路由/epoch。禁止 SQLite/PG 双主、禁止将 PG 新状态盲写回 SQLite、禁止对账失败自动修复。

## 9. Evidence policy

| 级别 | 含义 | 可证明内容 |
|---|---|---|
| L0 | AST/source/spec | 事实候选与调用图 |
| L1 | SQLite 单测 | SQLite 语义 |
| L2 | 隔离 PG 单连接 | 方言/局部 DDL |
| L3 | 隔离 PG 多连接/故障 | lease/counter/recovery/restore 原型 |
| L4 | 开发灰度/真实链路 | 部署对象和用户可见结果 |

任何 release gate 需要 L3；writer/cutover 需要 L4；L0/L1 不得升级为验收。

## 10. Cross-task contracts

| Task | 提供 | 消费 | 完成信号 |
|---|---|---|---|
| postgres-migration | schema/counter/lease/CAS/settle/recovery/reconciliation | 所有后续任务 | PG multi-connection + restore |
| control-plane-fault-domain | pools/reserve/recovery/health | load/provider/operations | starvation matrix |
| provider-reliability-boundaries | stream quota/circuit/deadline/egress | control/load | upstream fault matrix |
| redis-coordination | admission/cache/wakeup/degradation | runtime/load | Redis outage matrix |
| lexical-search-migration | PGroonga/index lifecycle/golden corpus | RAG/load | recall/latency/revocation |
| pgvector-rag | embedding/vector/revision/citation lifecycle | RAG/load | failure/rebuild/version matrix |
| postgres-operations-cutover | pool/PITR/HA/epoch/rollback | migration/load | backup/restore/cutover |
| multitenant-load-acceptance | account×space×kind final matrix | parent release | all required cells green |

未完成提供方不能由消费方临时实现替代；消费方必须记录 blocker 并执行其他独立 slice。
