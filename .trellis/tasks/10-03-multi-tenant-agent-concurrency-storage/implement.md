# 实施计划：多租户并发与存储架构重设计

> 本计划仅在设计评审通过后执行。当前任务保持 planning，不启动 `task.py start`。

## Phase 0：基线与决策门

- [x] 固化当前运行拓扑：API listeners、sidecar kind、maintenance、worker/thread/DB pool、SQLite WAL/锁行为。
      证据：`10-06-architecture-completion-orchestration/research/evidence/c0-environment.md`（环境 manifest）。
- [ ] 采集当前按 account/space/kind 的并发、队列、run 时长、retry、tool、lease 和控制面延迟基线。
- [x] 建立 PostgreSQL、Redis、pgvector 的隔离开发服务和最小健康/故障注入矩阵，不接入业务流量。
      证据：dev 镜像 `familygraph-postgres:pg16-pgvector-pgroonga`；2026-10-23 在隔离库复跑
      `multitenant_matrix` / `cross_instance_capacity` / `pgroonga_*` / `pgvector_pipeline` / `redis_degradation`。
- [x] 完成 PostgreSQL 为真源、Redis 为加速层、pgvector-first 的评审决策；若否决，更新设计与故障语义。
      证据：三条决策均已落地为代码合同——`writer_epoch` 的阶段语义、`redis_accel` 的 `pg_fallback`
      策略（负缓存只能拒绝、永不授权）、`rag_embeddings` 的 pgvector-first。
- [x] 识别可拆分子任务：调度/资源隔离、PostgreSQL 迁移、Redis admission、RAG/pgvector、压测/可观测性。
      证据：`10-03-postgres-migration`、`10-03-redis-coordination`、`10-03-pgvector-rag`、
      `10-04-control-plane-fault-domain`、`10-04-provider-reliability-boundaries`、
      `10-04-postgres-operations-cutover`、`10-04-lexical-search-migration`、
      `10-04-multitenant-load-acceptance` 八个子任务。

## Phase 0.5：执行前证明门（新增硬前置）

- [x] 完成 `10-05-migration-proof-gates`，并将其状态置为 verified，而不是仅创建文档。
      证据：已归档至 `archive/2026-10/10-05-migration-proof-gates`；其自述 Phase 3 仍有两项未勾选（见该任务）。
- [ ] 所有子任务在执行前完成 PRD/design/implement、证据等级、失败语义、回滚点和 owner。
- [x] 任何 PostgreSQL/Redis/pgvector 原型必须运行在隔离服务；建立环境 manifest，禁止把本地单元结果当作跨服务证据。
      证据：`c0-environment.md`；2026-10-23 所有探针均在隔离 database/Redis 上跑、跑完即 DROP。
- [ ] 对每个关键不变量建立正向、负向、mutation、双连接并发和故障恢复用例。
- [ ] 全局锁序、连接预算、控制面保留容量、重试预算和跨任务接缝必须在代码前冻结。
- [ ] Gate 未通过时，父任务只能更新设计/证据，不得进入业务实现或开发切换。


- [x] 定义 `account_id` Assistant、`space_id` Steward、global/kind/control-plane 的 quota key 与持久/运行时状态。
      证据：`capacity.RESOURCE_ASSISTANT_RUN`(account) / `RESOURCE_STEWARD_JOB`(space) /
      `RESOURCE_CLUSTER_TOOL` / `RESOURCE_PROVIDER_STREAM`(global+kind+tenant 三层)。
- [ ] 设计 fair queue、tenant credits、最大排队、拒绝、取消、回收、饥饿避免和优先级规则。
- [x] 将 heartbeat/lease/context/settle/cancel/health 与 model/tool/background 分离到独立 limiter/保留容量。
      证据：`config._validate_agent_execution_admission` 强制
      `AGENT_EXECUTION_GLOBAL_CAPACITY ≤ POOL_MAX_CONNECTIONS-1`，为控制面留出连接与工作线程余量。
- [ ] 为 provider retry 建立单一 run-level RetryBudget，消除 provider×session 乘法重试。
- [x] 添加无敏感数据的 admission/queue/retry/saturation 指标和诊断事件。
      证据：`agent_admission.py`；`RedisHealth` 只暴露 `available`/`policy`/`last_error`/`checked_at`；
      `agent_provider_egress` 的 detail 只含 `error_class`/`retryable`/`sent`。
- [x] 用测试先证明：单个 account/space 突发不能占满其他 tenant/kind/control-plane 容量。
      证据：2026-10-23 `multitenant_matrix.py` 第 2 项「隔离性」——account 1 满载时
      account 2 立即可得，无饥饿；含「无行锁形态越限」反证。

## Phase 2：Assistant/Steward 执行隔离

- [x] 先实现同 backend/sidecar 内的 Assistant、Steward、tool、background 分池和独立上限。
      证据：`capacity` 的 `agent_kind` 维度独立；`stream_specs` 中 assistant/steward 分列。
- [x] 评估并实现 sidecar 分进程或独立 worker pool；明确 run token、lease、cancel、graceful shutdown 和恢复语义。
      证据：`docker-compose.yml` 的 `agent-assistant` / `agent-steward` 两个独立服务各持单角色
      （`FG_AGENT_ROLE`），`agent-combined` 需显式 `--profile combined`；
      `agent/test/deployment-split.test.ts` 5 条断言。
- [ ] 验证 sidecar event loop 不因单一 model stream/tool burst/retry storm 阻塞其他 kind。
- [x] 保持 sidecar 无业务 DB、无外网 egress 和现有 internal listener 隔离。
      证据：架构约束（记忆 #333）：sidecar 只做模型循环与受控工具编排，Web 出网由 FastAPI 独占。

## Phase 3：PostgreSQL schema 与事务实验

- [ ] 盘点所有 SQLite-specific SQL、`BEGIN IMMEDIATE`、PRAGMA、FK、CHECK、partial index、唯一约束、迁移 downgrade 和测试 fixture。
- [x] 在隔离 PostgreSQL 中建立 schema prototype：run/event/attempt/lease/settle/audit/queue 与关键领域表。
      证据：`pg_baseline_build.py`；开发库实测 91 表；`real_snapshot_import.py` 导入真实快照 87 表 / 143,134 行。
- [ ] 设计 `FOR UPDATE SKIP LOCKED`/持久 ready queue、CAS、幂等、lease renewal/recovery 和并发索引。
- [x] 对照验证现有 egress/fence/settle/recovery 语义，特别是每次真实 egress 恰好一条审计和 `sent` 判定。
      证据：2026-10-23 开发库对账——egress 覆盖 1441 个 run、每次尝试恰好一条、
      `sent=false` 均带 `error_class`；attempt/run 无孤儿。
- [x] 形成 SQLite→PostgreSQL 导出/导入校验、双读/双写或 shadow 方案；为每阶段写 refusal guard、rollback 和备份步骤。
      证据：`writer_epoch.WRITER_STAGES = (sqlite, shadow, pg_control, pg_all)`，
      `postgres_is_authoritative` 只在 pg_control/pg_all 为真（shadow 明确不算真源）；
      `import_reconcile_probe.py` 的重复导入/断点重试；refusal 语义为「只报告不自动修复」。
- [ ] 先在隔离开发数据库执行 migration/restore/并发测试，禁止接触线上数据库。

## Phase 4：Redis 与 pgvector 原型

- [x] Redis 只实现可丢失 admission/token bucket/cache/wakeup 原型，定义 TTL、key scope、故障降级和不放权原则。
      证据：`redis_accel.py` 的负缓存（**刻意不用 token bucket**：令牌由 Redis 授权就等于把 Redis
      变成配额真源，与 `pg_fallback` 矛盾）；`scoped_key` 强制 scope+epoch；
      2026-10-23 降级矩阵见 `10-03-redis-coordination/research/evidence/redis-degradation-matrix.md`。
- [ ] 验证 Redis 故障、重启、重复消息、时钟漂移和 PostgreSQL 恢复后的行为。
- [x] pgvector 原型绑定 source/document/chunk/revision/space/scope/visibility/citation，验证撤权、删除、重建和过滤。
      证据：`rag_embedding_segments`(chunk_id FK, model, revision, scope)；
      2026-10-23 `pgvector_pipeline_probe.py` 全链路 PASS（含 filter-then-ANN 反证与 RRF 确定性）。
- [x] 用基准决定 pgvector 是否满足第一阶段规模；只有失败证据才创建独立向量服务子任务。
      证据：2026-10-23 十万行 / 512 维：HNSW 默认 `ef_search=40` 下 recall 0.9600、p50 0.93ms
      （比精确检索快约 450 倍），构建 120.5s / 260.4MB。**达标 → 不建独立服务。**

## Phase 5：多租户并发验收

- [ ] 构造矩阵：N 个 account Assistant、M 个 space Steward、同用户跨空间、同空间多用户、tool burst、长 stream、provider slow/fail、RAG/index、取消/撤权、重启/recovery。
- [ ] 注入 provider connect timeout、header timeout、stream interruption、DERP/网络不可达、Redis down、PostgreSQL failover、SQLite legacy contention。
- [ ] 验收控制面 p95/p99、tenant queue wait、run total deadline、retry 次数、数据库事务等待、错误隔离和用户可见结果。
- [ ] 运行 backend/agent/full relevant checks，记录未运行的前端/高成本检查。
- [ ] 变异验证：移除 tenant quota、移除 control-plane reserve、恢复乘法 retry、绕过 PostgreSQL CAS、取消 visibility filter 时测试必须失败。

## Phase 5.5：跨子任务问题闭合

- [ ] `10-04-control-plane-fault-domain`：完成 control/execution/background/admin 分池、DB reserve、sidecar 故障域、优雅停机和多实例 recovery。
- [x] `10-04-provider-reliability-boundaries`：完成 stream-level quota、长流 deadline、backpressure、连接生命周期和 upstream/kind/tenant circuit。
      证据：`capacity.try_acquire_stream` 接入 `internal_agent.py:689`；`provider_proxy` 的墙钟 deadline；
      `provider_circuit.py` 按 provider+kind 分区；`c4-stream-limits.md` 记录背压机制与守护断言。
- [ ] `10-04-postgres-operations-cutover`：完成连接预算、PgBouncer 兼容性、backup/WAL/PITR、writer epoch、保留/归档和开发切换回滚。
- [x] `10-04-lexical-search-migration`：完成 FTS5 trigram 的 PGroonga/Unicode n-gram 对照语料和生产候选选择。
      证据：2026-10-23 四方对照——PGroonga 10/10 与真源精确一致，`pg_trgm` 对 CJK 全部漏检，
      Unicode n-gram 假阳性最多 +4。选择 PGroonga 主路径 + n-gram 后备。
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

## 2026-10-23 复核

上表已按**实际证据**更新。原文件长期显示 0/45，导致本任务与 `10-06` 编排任务把大量
已完成工作当未完成，进而**阻塞**了 lexical / pgvector 两个子任务。

**勾选口径**：只有存在**可直接引用的证据**（测试名、探针输出、落盘文件）才勾选。
「工具已交付但审查未做完」「探针部分覆盖」一律**不勾选**，并在条目下写明「仍未做」。

**父任务仍未闭合的原因**：剩余条目集中在 Phase 5（真实多租户矩阵与尾延迟）、
Phase 5.5 的部分子任务、Phase 6（灰度收尾）。这些需要**真实多实例部署与线上发布**，
属本任务 PRD 的停止条件，须由用户决定时机。

**不得据子任务探针通过宣称「多租户并发已达标」**：探针证明的是配额守恒、故障收敛
与索引性能，不是尾延迟与用户可见结果。
