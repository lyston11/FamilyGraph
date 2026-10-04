# 实施计划：多租户并发与存储架构重设计

> 本计划仅在设计评审通过后执行。当前任务保持 planning，不启动 `task.py start`。

## Phase 0：基线与决策门

- [ ] 固化当前运行拓扑：API listeners、sidecar kind、maintenance、worker/thread/DB pool、SQLite WAL/锁行为。
- [ ] 采集当前按 account/space/kind 的并发、队列、run 时长、retry、tool、lease 和控制面延迟基线。
- [ ] 建立 PostgreSQL、Redis、pgvector 的隔离开发服务和最小健康/故障注入矩阵，不接入业务流量。
- [ ] 完成 PostgreSQL 为真源、Redis 为加速层、pgvector-first 的评审决策；若否决，更新设计与故障语义。
- [ ] 识别可拆分子任务：调度/资源隔离、PostgreSQL 迁移、Redis admission、RAG/pgvector、压测/可观测性。

## Phase 1：资源模型与控制面优先

- [ ] 定义 `account_id` Assistant、`space_id` Steward、global/kind/control-plane 的 quota key 与持久/运行时状态。
- [ ] 设计 fair queue、tenant credits、最大排队、拒绝、取消、回收、饥饿避免和优先级规则。
- [ ] 将 heartbeat/lease/context/settle/cancel/health 与 model/tool/background 分离到独立 limiter/保留容量。
- [ ] 为 provider retry 建立单一 run-level RetryBudget，消除 provider×session 乘法重试。
- [ ] 添加无敏感数据的 admission/queue/retry/saturation 指标和诊断事件。
- [ ] 用测试先证明：单个 account/space 突发不能占满其他 tenant/kind/control-plane 容量。

## Phase 2：Assistant/Steward 执行隔离

- [ ] 先实现同 backend/sidecar 内的 Assistant、Steward、tool、background 分池和独立上限。
- [ ] 评估并实现 sidecar 分进程或独立 worker pool；明确 run token、lease、cancel、graceful shutdown 和恢复语义。
- [ ] 验证 sidecar event loop 不因单一 model stream/tool burst/retry storm 阻塞其他 kind。
- [ ] 保持 sidecar 无业务 DB、无外网 egress 和现有 internal listener 隔离。

## Phase 3：PostgreSQL schema 与事务实验

- [ ] 盘点所有 SQLite-specific SQL、`BEGIN IMMEDIATE`、PRAGMA、FK、CHECK、partial index、唯一约束、迁移 downgrade 和测试 fixture。
- [ ] 在隔离 PostgreSQL 中建立 schema prototype：run/event/attempt/lease/settle/audit/queue 与关键领域表。
- [ ] 设计 `FOR UPDATE SKIP LOCKED`/持久 ready queue、CAS、幂等、lease renewal/recovery 和并发索引。
- [ ] 对照验证现有 egress/fence/settle/recovery 语义，特别是每次真实 egress 恰好一条审计和 `sent` 判定。
- [ ] 形成 SQLite→PostgreSQL 导出/导入校验、双读/双写或 shadow 方案；为每阶段写 refusal guard、rollback 和备份步骤。
- [ ] 先在隔离开发数据库执行 migration/restore/并发测试，禁止接触线上数据库。

## Phase 4：Redis 与 pgvector 原型

- [ ] Redis 只实现可丢失 admission/token bucket/cache/wakeup 原型，定义 TTL、key scope、故障降级和不放权原则。
- [ ] 验证 Redis 故障、重启、重复消息、时钟漂移和 PostgreSQL 恢复后的行为。
- [ ] pgvector 原型绑定 source/document/chunk/revision/space/scope/visibility/citation，验证撤权、删除、重建和过滤。
- [ ] 用基准决定 pgvector 是否满足第一阶段规模；只有失败证据才创建独立向量服务子任务。

## Phase 5：多租户并发验收

- [ ] 构造矩阵：N 个 account Assistant、M 个 space Steward、同用户跨空间、同空间多用户、tool burst、长 stream、provider slow/fail、RAG/index、取消/撤权、重启/recovery。
- [ ] 注入 provider connect timeout、header timeout、stream interruption、DERP/网络不可达、Redis down、PostgreSQL failover、SQLite legacy contention。
- [ ] 验收控制面 p95/p99、tenant queue wait、run total deadline、retry 次数、数据库事务等待、错误隔离和用户可见结果。
- [ ] 运行 backend/agent/full relevant checks，记录未运行的前端/高成本检查。
- [ ] 变异验证：移除 tenant quota、移除 control-plane reserve、恢复乘法 retry、绕过 PostgreSQL CAS、取消 visibility filter 时测试必须失败。

## Phase 5.5：跨子任务问题闭合

- [ ] `10-04-control-plane-fault-domain`：完成 control/execution/background/admin 分池、DB reserve、sidecar 故障域、优雅停机和多实例 recovery。
- [ ] `10-04-provider-reliability-boundaries`：完成 stream-level quota、长流 deadline、backpressure、连接生命周期和 upstream/kind/tenant circuit。
- [ ] `10-04-postgres-operations-cutover`：完成连接预算、PgBouncer 兼容性、backup/WAL/PITR、writer epoch、保留/归档和开发切换回滚。
- [ ] `10-04-lexical-search-migration`：完成 FTS5 trigram 的 PGroonga/Unicode n-gram 对照语料和生产候选选择。
- [ ] `10-04-multitenant-load-acceptance`：用统一矩阵验证新增子任务没有互相破坏，形成最终 release gate。

## Phase 6：灰度与收尾

- [ ] 仅在开发环境切换一小类 control-plane/attempt 流量，核对业务计数、审计、scope、run/attempt 状态和数据摘要。
- [ ] 完成一致性对账、备份恢复演练、回滚演练和长时间观察；不自动发布线上。
- [ ] 更新 backend/architecture/RAG/agent runtime 规范和部署手册，记录 SQLite 过渡模式上限。
- [ ] 将不能在一个任务中安全完成的迁移/Redis/向量/worker 子任务拆出并写明依赖。

## Required validation gates

- 每一阶段都有隔离环境和可回滚点；不得以单元测试代替真实多租户矩阵。
- PostgreSQL migration 必须先执行 refusal guards，再移动 Alembic 版本；必须验证 downgrade/restore 不破坏已修正数据。
- backend：相关 pytest、ruff、mypy；完整门禁按最终改动范围执行。
- agent：type-check、lint、测试、build；验证 Assistant/Steward 独立资源池和 token/lease 兼容。
- RAG/vector：授权、scope、revision、citation、撤权和重建回归。
- 交付前必须有开发环境真实并发证据；线上只提供手动发布包和回滚步骤。

## Rollback points

- R0：只新增诊断/影子指标，不改变执行路径。
- R1：limiter/RetryBudget 可由配置关闭或回退旧路径，但不得回退到无界重试。
- R2：sidecar 分池失败时回退同进程分池，保留 control-plane reserve。
- R3：PostgreSQL shadow/import 校验失败时不切 writer，SQLite 保持只作为当前开发过渡；禁止双主写。
- R4：Redis/pgvector 原型失败时禁用加速层，PostgreSQL/确定性路径继续工作。
- R5：灰度期间只回退流量路由，不删除已导入数据，不降级授权/审计合同。
