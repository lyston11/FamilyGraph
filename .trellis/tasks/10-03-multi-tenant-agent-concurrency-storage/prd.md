# Assistant 与 Steward 多租户并发及存储架构重设计

## Goal

为每个用户的 Assistant 与每个家庭空间的 Steward 建立真正可并发、可隔离、可公平调度的执行架构，并把当前 SQLite 单写者存储升级为支持多用户并发读写、租约协调、审计和未来检索扩展的持久化基础设施。

用户价值：一个用户或空间的长模型请求、工具突发、上游重试或数据维护不能拖慢其他用户的 Assistant、其他空间的 Steward，也不能让 heartbeat/lease/settle 等控制面因执行面拥塞而失效。

## Background / confirmed facts

### 当前已经具备的逻辑隔离

- Assistant 与 Steward 使用不同的 runtime kind、token claims、provider resolution、tool allowlist 和 execution fence。
- Assistant 以 `account_id` 为主要执行主体；Steward child run 以 `space_id`/attempt 为主要执行主体，不进入 Assistant 通用队列。
- Provider egress 统一经 FastAPI gateway；sidecar 不持有数据库、业务凭据或外网出口。
- Steward 模型辅助的执行单元是 `steward_model_calls` attempt；当前每空间 attempt 并发默认 2，sidecar 另有 Assistant 与 Steward 槽位预算。

### 当前缺口

- Assistant 与 Steward 可运行在同一个 sidecar 进程，共享 Node event loop、进程内 worker/HTTP 资源和内存。
- backend 由同一个 FastAPI 进程承载浏览器、internal agent、admin listener；执行请求、控制请求和维护任务共享 AnyIO worker、SQLAlchemy pool、SQLite 文件和写入者。
- 当前 `DATABASE_URL` 为 SQLite；连接池显式为 size=5、overflow=10、pool timeout=30s，总连接上限 15。WAL 提高读并发但 SQLite 仍只有单写者。
- 当前代码使用 `BEGIN IMMEDIATE` 覆盖必须原子化的 check-then-act；迁移和并发测试依赖 SQLite 的约束、FK、WAL 和 writer 语义。
- 当前 agent sidecar 环境是单实例角色配置（可为 `assistant`、`steward` 或 `both`），Assistant 每账户最多 2 个并发 run；Steward assist 是 per-space 上限，不是完整的 account/space/global 公平调度体系。
- Provider request retry 与 session retry 叠加；暂时故障在当前配置下最多形成 6×4=24 次真实 egress。Tailscale/DERP 上游不可达时，这会把外部故障放大为数分钟 run 和大量无效资源占用。
- 现有 RAG 是 SQL/FTS 生命周期；Steward 当前不接入私有 RAG。未来若引入 embedding/vector search，不能绕过既有 source/revision/scope/visibility/context-build 信任边界。

## Requirements

### R0 多租户资源模型

定义并实现资源主体和隔离层级：

- Assistant 资源主体为 `account_id`，Steward 资源主体为 `space_id`；同一用户跨空间不能误共享 Steward 配额。
- 至少支持 global、account、space、agent-kind 四层预算；Assistant 与 Steward 不能互相挤占保留容量。
- 明确每个预算的排队、拒绝、超时、取消、回收和公平规则；不可用资源不得通过无界排队伪装为成功。
- 运行状态、租约、取消、结算、审计和错误收敛必须保持现有 per-run/per-attempt 授权边界。

### R1 控制面优先

heartbeat、lease、context、settle、cancel、health 等控制请求必须拥有独立或保留的执行容量，不得和模型流、工具执行、RAG/索引维护共享一个可被耗尽的队列。

控制面在执行面过载时仍须能够：

- 续租或明确判定失租；
- 处理取消和撤权；
- 结算终态；
- 记录安全审计与资源诊断。

### R2 Assistant / Steward 执行隔离

至少在 limiter/worker-pool 层隔离 Assistant 与 Steward；设计中评估 sidecar 分进程或分池的最终形态。一个 space 的 Steward 批量计算不得消耗其他 account Assistant 的全部资源。

### R3 数据库与协调存储

- 评估并规划从 SQLite 迁移到支持并发读写和可靠租约协调的主数据库；目标必须支持多用户并发读取、写入、事务、索引、行级/适当粒度锁和连接池隔离。
- PostgreSQL 作为推荐的系统记录候选，必须明确迁移范围、schema/constraint 保留、事务语义、租约/CAS 实现、备份恢复、开发/生产部署和回滚策略。
- Redis 仅承担适合的短生命周期协调/限流/缓存/队列用途，不得在没有持久化和幂等设计时成为业务事实来源；必须定义 Redis 不可用时的 fail-closed/degraded 行为。
- 向量能力优先评估 PostgreSQL + pgvector 是否足以承载第一阶段，只有明确的规模、检索或运维理由成立时才引入独立向量数据库；向量索引不得取代 Memory/RAG 的 source、revision、scope、visibility 和引用合同。
- 迁移过程中不得直接复制正在运行的 SQLite 主库；遵守隔离快照、备份、refusal guard 和回滚约束。

### R4 重试与故障域

- 为每个 run 定义统一 retry/time budget，避免 provider request retry 与 session retry 乘法无限放大。
- 区分连接不可达、上游暂时错误、上游永久拒绝、流中断、取消/失租和策略阻断；每类定义是否重试、最大次数、总墙钟预算和 circuit-breaker/退避行为。
- 单一 provider/DERP/空间故障不得阻塞其他空间；重试资源必须按 upstream、kind、account/space 预算隔离。

### R5 可观测性与验收

按 `agent_kind`、`account_id`（脱敏/哈希或安全维度）、`space_id`（安全维度）、run/attempt 和阶段记录：

- admission/queue wait；
- provider request/header/stream/retry；
- tool wait/execution；
- DB connection/transaction wait；
- control-plane latency；
- lease renewal/expiry；
- per-tenant/global saturation；
- RAG/index/vector work。

不得记录 prompt、模型正文、token、凭据、SQL 参数或原始个人数据。

### R6 兼容与迁移

- 现有 internal agent 协议、run token、Assistant/Steward kind、egress 审计、fence、settle 两阶段语义保持兼容，除非设计明确列出版本化迁移。
- 迁移必须支持灰度、双读/双写或可验证导出导入的明确阶段；每阶段有一致性检查、停机/回滚边界和数据恢复方案。
- SQLite 仍可作为开发/降级过渡存储时，必须明确它的能力上限，不得继续把它当成目标多租户协调数据库。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| AC-1 | 设计文档明确 Assistant(account) 与 Steward(space) 的资源主体、global/account/space/kind 配额、排队公平和拒绝/取消/回收合同。 |
| AC-2 | 控制面在模型流、工具突发、维护任务和 provider retry 过载时仍能在有界时间内完成 heartbeat/lease/cancel/settle；有并发回归矩阵。 |
| AC-3 | Assistant 与 Steward 在 sidecar/backend 执行资源上有可证明的隔离；单一 account/space 的突发不能占满另一类 agent 的保留容量。 |
| AC-4 | PostgreSQL（或经证据证明等价的并发数据库）迁移方案覆盖 schema、约束、事务、租约/CAS、索引、备份恢复、灰度、回滚和开发/生产隔离；明确 SQLite 退出主协调路径的阶段。 |
| AC-5 | Redis 的职责、数据生命周期、不可用行为和一致性边界明确；向量数据库方案以 pgvector-first 与独立服务的证据对比，并保留 RAG source/revision/scope/visibility/citation 合同。 |
| AC-6 | retry/time budget 不再形成无界或乘法放大；DERP/provider 不可达时单个 run 在有界预算内失败，其他租户的控制面和执行面不被拖死。 |
| AC-7 | 真实多用户并发矩阵覆盖：多个 account Assistant、多个 space Steward、同用户跨空间、同空间多用户、tool burst、provider slow/fail、RAG/index work、取消/撤权、重启和租约恢复。 |
| AC-8 | 安全资源诊断可按租户与阶段定位排队/饱和/失败原因，且无 prompt、正文、凭据、SQL 参数或原始个人数据泄露。 |
| AC-9 | 现有 backend/agent 协议与 egress/fence/settle/RAG 回归通过；迁移与恢复在隔离环境完成；线上发布仍由用户手动执行。 |

## Accepted architecture baseline

用户已接受以下基线（2026-10-03）：

```text
PostgreSQL = 唯一持久事实、租约/attempt 协调与事务真源
Redis      = 短生命周期 admission、限流、缓存、wakeup 加速层
pgvector   = 第一阶段向量检索优先方案
独立向量库 = 只有规模/延迟/过滤/运维基准证明 pgvector 不足时才引入
```

Redis 不承载 run 终态、attempt 结算、lease 真相、授权事实或审计；向量索引不取代 source/revision/scope/visibility/citation。SQLite 仅作为迁移过渡，退出目标多租户协调主路径。

## Parent / child task map

本父任务负责共同合同、依赖关系、跨子任务集成和最终多租户验收；实现工作拆为以下独立子任务：

1. `10-03-agent-resource-isolation`：Assistant(account)/Steward(space) 配额、公平调度、control-plane 保留容量、执行池隔离、RetryBudget。它可以先以当前 SQLite 做运行时实验，但不得把 SQLite 宣称为最终并发存储。
2. `10-03-postgres-migration`：PostgreSQL schema、事务/锁/CAS、lease/settle 迁移、历史导入、校验、灰度和回滚。它必须保留现有 Agent/RAG 授权合同，并为资源调度提供持久真源。
3. `10-03-redis-coordination`：PostgreSQL 真源之上的 Redis admission/token bucket/cache/wakeup；依赖资源 key 合同，但不得把 Redis 变成 lease 真源。
4. `10-03-pgvector-rag`：pgvector-first 原型与基准；依赖 PostgreSQL schema 和现有 RAG source/revision/scope/visibility/citation 合同，只有证据不足才另开独立向量库子任务。

子任务之间的等待关系必须写在各自工件中；父任务不把任务树顺序当作隐式依赖。所有子任务最终必须汇入同一套 account/space/kind/control-plane 资源合同和并发验收矩阵。


- 多租户资源模型、调度与公平性设计；
- Assistant/Steward sidecar 与 backend 的 worker/limiter/控制面隔离；
- PostgreSQL 主数据库迁移路线与事务/租约重设计；
- Redis 的协调、缓存、限流和故障边界；
- pgvector-first 与独立向量数据库的选型边界；
- retry/circuit-breaker/time budget；
- 诊断指标、并发压测和故障注入验收矩阵；
- 分阶段迁移、回滚、备份和开发环境验证。

## Out of Scope

- 本任务直接实现所有业务 API 或重新设计家庭域授权；
- 把 sidecar 变成可访问数据库的服务；
- 绕过 Provider gateway 或放宽数据出境/可见性策略；
- 用自动重启替代资源隔离和故障收敛；
- 在没有规模和检索证据时直接引入独立向量数据库；
- 线上自动迁移、线上自动发布或修改线上环境；
- 删除现有 egress、fence、settle、RAG 引用和审计合同。

## Risks / Deferred Decisions

- PostgreSQL 迁移会触及所有 SQLAlchemy/Alembic 事务和并发测试；必须先做隔离数据库实验，再决定是否分拆子任务。
- Redis 若被用于 lease/queue，必须避免 Redis 状态与 PostgreSQL 事实分裂；第一阶段优先让 PostgreSQL 持久化状态为真源。
- SQLite → PostgreSQL 的双写一致性和历史数据导入是独立工作量，不能隐藏在调度器重构中。
- 如果开发环境暂时仍保留 SQLite，必须把它明确标记为过渡模式，并限制并发验收目标，不得宣称已经达到多用户生产并发。

## Accepted architecture decision

用户已确认采用以下基线：**PostgreSQL 作为唯一持久事实与租约协调真源；Redis 只做短生命周期限流、缓存、wakeup 和 admission 加速；第一阶段向量检索优先使用 pgvector，不立即引入独立向量数据库。**

后续设计若要改变该基线，必须提供规模、延迟、过滤能力、运维或故障恢复的证据，并更新父任务与相关子任务的故障语义、迁移范围和验收标准。
