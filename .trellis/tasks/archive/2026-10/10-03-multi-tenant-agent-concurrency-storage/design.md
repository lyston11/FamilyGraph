# 技术设计：Assistant/Steward 多租户并发与存储架构

## 1. 设计原则

### 1.1 持久事实、协调加速、检索索引三者分离

```text
PostgreSQL
  └─ durable domain state / agent run / attempt / lease / settle / audit / projection

Redis（可选加速层）
  └─ short-lived admission / token bucket / cache / wakeup / pub-sub

pgvector（第一阶段优先）
  └─ PostgreSQL 内的 embedding/index，加上 source/revision/scope/visibility 外键与过滤

独立向量数据库（仅在证据达标后）
  └─ 大规模 ANN 或专门检索负载，不作为业务事实来源
```

PostgreSQL 是唯一持久事实和 lease/attempt 终态真源。Redis 丢失、重启或网络不可达时，不能导致业务状态与租约终态分裂；必要时回退到 PostgreSQL 有界 admission 或 fail closed。向量索引可重建，任何检索结果必须回到现有 ContextBuild 和可见性授权合同。

### 1.2 资源隔离先于水平扩展

为每次执行计算以下 resource key：

- Assistant：`global + kind=assistant + account_id`；
- Steward：`global + kind=steward + space_id`；
- 控制面：`global + control-plane`；
- provider：`upstream/provider profile + kind + tenant key`；
- tool：`kind + tenant key + tool class`；
- RAG/index：`background + space_id`。

每个 key 都有并发上限、队列上限、最大等待时间、取消行为和计费/审计字段。全局 cap 不能被单个 account 或 space 霸占；每个 kind 和 control plane 需保留容量。

## 2. 运行时分层

### 2.1 Control plane

负责：lease、heartbeat、context、token renewal、cancel、settle、health、recovery。

特点：

- 独立 worker/capacity limiter，或者至少有硬保留名额；
- 不等待 model stream、tool burst、RAG/index；
- 所有操作短事务；
- 失去 Redis 时仍以 PostgreSQL 的 lease/attempt 状态裁决；
- deadline、retry、cancel 都是有界的。

### 2.2 Model execution plane

负责 Assistant/Steward 的 Pi session 和 provider stream。

- Assistant 和 Steward 至少有不同 limiter；
- 长 provider stream 不持有 DB transaction；
- stream chunk 的 gate/fence 查询使用独立短生命周期 Session，并在线程/异步 DB 执行器中完成；
- session retry 和 provider retry 由一个 run-level budget 统一裁决；
- 上游不可达时使用有限重试和 circuit breaker，不把失败扩大成 24 次嵌套 egress。

### 2.3 Tool plane

- tool admission 以 account/space/kind 分层，另有 global cap；
- 单个 run 的 tool burst 不能占满所有工具容量；
- 控制面容量不得被 tool worker 等待耗尽；
- tool 事务只包含授权 fence、幂等占位、结果/审计写入等必要短步骤；
- 工具结果仍必须走现有 scope、闭合 schema、viewer/steward fence。

### 2.4 RAG/index plane

- 维护任务使用低优先级、可暂停、可回压的 background budget；
- 每 space 有限并发和队列；
- 不与 control plane 共用不可区分的 worker 名额；
- embedding/vector 写入不能绕过 revision、visibility、scope 和 citation。

## 3. PostgreSQL 目标设计

### 3.1 第一阶段职责

PostgreSQL 承担：

- 家庭域和成员/授权数据；
- Agent run、event、token scope 元数据、attempt、lease、settle；
- provider egress、tool execution、审计；
- Steward plan、projection、ActionCard；
- Memory/RAG source、revision、document/chunk 元数据；
- pgvector embedding（若第一阶段容量验证通过）。

### 3.2 并发语义

- 用 PostgreSQL 行锁与 `FOR UPDATE SKIP LOCKED` 或等价持久 ready queue 实现租约选择；
- lease acquire、renew、release/recovery 与 attempt 状态转移必须是短事务；
- 使用 PostgreSQL 约束、唯一索引和 `ON CONFLICT` 保留 CAS/idempotency；
- 评估将 SQLite `BEGIN IMMEDIATE` 映射为行锁/事务隔离，不机械复制语句；
- 对高频 append/event/audit 表设计索引、分区或批写边界，但不能牺牲每次真实 egress 恰好一条审计合同；
- 连接池按 control/model/tool/background 规划，不让 pool overflow 成为未观测的队列。

### 3.3 迁移阶段

```text
M0  schema/SQL compatibility inventory + isolated PostgreSQL
M1  PostgreSQL schema + refusal guards + import verifier
M2  read-only shadow / dual-read comparison
M3  controlled dual-write for selected append-only paths
M4  lease/agent control plane cutover
M5  browser/domain write cutover
M6  disable SQLite as primary writer after retention/rollback window
```

每一步必须有：行数/摘要/hash 校验、授权/可见性校验、run/attempt/lease 一致性校验、备份、回滚点和开发环境验收。线上发布由用户手动执行；不能在运行中的 SQLite 主库上直接复制文件做迁移。

## 4. Redis 边界

### 4.1 允许职责

- 短期 token bucket 和 tenant admission cache；
- scheduler wakeup/pub-sub；
- 可重建的缓存；
- provider circuit-breaker 的短期统计；
- 非事实性的排队提示。

### 4.2 禁止职责

- 不把 run 终态、attempt 结算、lease 真相、授权事实或审计只放 Redis；
- 不允许 Redis key TTL 单独决定业务状态；
- 不用 Redis lock 替代 PostgreSQL 的持久 CAS/唯一约束；
- Redis 不可用时不自动放开额度，避免 fail-open。

## 5. 向量检索边界

先做 pgvector-first 的规模实验：

- embedding row 必须绑定 source/document/chunk、revision、space/scope 和 visibility policy version；
- 查询先取得授权 projection/filter，再执行向量相似度排序；
- 删除/revision/撤权必须能使旧向量不可见或可回收；
- embedding 更新是可重试、可暂停的 background job，不阻塞 agent control plane；
- 只有在向量规模、ANN 延迟、过滤能力、运维隔离或独立扩展需求超出 PostgreSQL 后，才引入独立 vector DB。

## 6. 重试与故障域

为每个 run 建立 `RetryBudget` 概念（具体实现可为持久字段或受保护的运行时状态）：

```text
absolute_deadline
max_provider_attempts
max_session_turns
max_total_retry_seconds
per-error-class budget
upstream circuit state
```

provider 层不能无条件重试后再由 session 层从头重试。一次连接失败应消耗同一个总预算；预算耗尽立即形成有界失败和审计，不继续占用 sidecar/backend。不同 upstream/profile 的 circuit state 必须隔离，不得全局熔断所有租户。

## 7. 兼容边界

保持不变：

- Assistant/Steward kind 和 token scope；
- internal agent API listener 隔离；
- Provider gateway 唯一 egress；
- egress audit 的每真实尝试一条语义；
- fence、幂等、settle 两阶段和 recover 合同；
- RAG source/revision/scope/visibility/citation 合同；
- sidecar 不访问业务数据库。

需要版本化的变化：

- lease/queue response 中新增 capacity/queue metadata；
- retry budget 错误码与 backoff 字段；
- 数据库连接/事务诊断字段；
- migration phase/readiness health contract。

## 8. 关键取舍

### PostgreSQL vs 继续 SQLite

推荐 PostgreSQL。SQLite 可继续作为单机开发/过渡模式，但不能作为目标多租户 agent coordination store；继续增加 pool 或 busy timeout 只会推迟单写者冲突。

### Redis 是否做 lease 真源

不推荐。lease 的持久事实、恢复和审计需要事务一致性，优先放 PostgreSQL；Redis 只做可丢失的加速和 admission。

### pgvector vs 独立向量服务

推荐第一阶段 pgvector：减少第二套事实系统、部署、授权同步和迁移复杂度。独立服务只有在基准证明 pgvector 无法满足规模/延迟/过滤/运维边界时再引入。

### 同进程分池 vs 分进程

短期先做 control/model/tool/background 的 limiter 和连接池边界，以便可验证迁移；长期 Assistant 与 Steward 分进程/独立 worker pool，建立故障域隔离。最终选择以并发矩阵和资源预算实验为依据，不凭默认值决定。

## 10. 新增跨子任务问题边界

本任务不把以下风险当作 PostgreSQL schema 的附带实现：

### 10.1 Control-plane / execution-plane 故障域

独立 limiter 不能自动等于独立 worker/DB pool。必须同时测量 AnyIO worker、SQLAlchemy checkout、事件循环、sidecar heap 和 provider stream；否则只是在一个共享队列外面再包一层 semaphore。`10-04-control-plane-fault-domain` 负责控制面保留容量、分池/分进程、重启和多实例 recovery。

### 10.2 Provider 长流与短请求

建连 admission、已建立的长流、工具执行和 control request 有不同时间尺度，不能共用一个名额或只看 connect timeout。`10-04-provider-reliability-boundaries` 负责 stream-level quota、backpressure、连接生命周期、upstream circuit 和长流 deadline；`RunRetryBudget` 只解决尝试次数乘法，不替代这些边界。

### 10.3 PostgreSQL 可运行性

数据库迁移成功不代表系统可运行。每实例 pool 总和、PgBouncer session 语义、WAL/PITR、故障切换、writer epoch、长事务和数据保留都会决定是否真正支持多租户。`10-04-postgres-operations-cutover` 负责这些运行和发布问题。

### 10.4 中文词法检索

RAG 的词法检索不是 pgvector 的附带项。当前 FTS5 trigram 的中文行为必须用 golden corpus 对照；`tsvector` 或 `pg_trgm` 不能未经证据替代。`10-04-lexical-search-migration` 负责 PGroonga-first 与 Unicode n-gram 后备。

### 10.5 集成容量与故障证据

单 run 成功、单实例 pytest 或单一数据库 benchmark 都不能证明多用户隔离。`10-04-multitenant-load-acceptance` 负责 account×space×kind 矩阵、故障注入、资源安全日志和最终切换 gate。

父任务的最终完成条件是：这些子任务的合同可以组合，且所有控制面、执行面、数据库、Provider、Redis、词法/向量索引和恢复证据在同一矩阵中不互相破坏。