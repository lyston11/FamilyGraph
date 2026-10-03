# 多租户并发与存储研究摘要

## 已确认的现状

### Agent 执行边界

- backend `app/config.py` 当前 `AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT=2`，`AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS=8`，`STEWARD_MAX_CONCURRENT_JOBS=4`，`STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE=2`。
- sidecar 以 `FG_AGENT_ROLE=assistant|steward|both` 运行，`AGENT_MAX_CONCURRENT_RUNS` 默认 2；Assistant/Steward 可能位于同一 Node 进程。
- Steward Pi child run 的 attempt、lease、heartbeat、context、provider、tool、settle 经过 internal API 和 token/fence；sidecar 不持有业务 DB。
- 同一个 run 的模型 turn、工具结果和下一轮模型调用必须依赖前一轮，属于必要的语义串行；不同 run 的资源并发不能依赖这种串行性来保证隔离。
- provider request retry 与 session retry 在历史现场形成最多约 6×4=24 次 egress；因此必须在架构层建立 run 总 retry/time budget，而不是只调单层重试。

### Backend 与 SQLite 边界

- `backend/app/config.py` 当前 `DATABASE_URL` 为 `sqlite:///<DATA_DIR>/db/app.db`。
- `backend/app/db.py` 显式配置 QueuePool：size=5、max_overflow=10、pool timeout=30s，总连接上限 15；连接启用 WAL、foreign_keys、busy_timeout、synchronous=NORMAL。
- SQLite WAL 允许读写重叠的一部分路径，但 SQLite 仍是单写者；现有 lease/CAS、`BEGIN IMMEDIATE`、fence、settle、audit 和 projection 写路径不能简单按 PostgreSQL 读写并发假设迁移。
- backend 同一服务承载家庭 API、internal agent API、admin API 和维护任务；控制面与执行面需要显式容量隔离，不能仅依赖同一个 AnyIO/SQLAlchemy 资源池。
- 项目规则要求迁移先在隔离数据库执行 refusal guards 与 `alembic upgrade head`，SQLite 运行期间不可直接复制主库，线上环境不得自动操作。

### RAG / 向量边界

- 当前 Memory/RAG 合同把 SourceFact、RawRelationInput、memory source、RAGDocument/RAGChunk、ContextBuild 和 AgentMessage 投影分层；向量索引不能取代 source、revision、scope、visibility 或 citation。
- 当前任务目标不是立刻接入独立向量库；先比较 PostgreSQL + pgvector、独立向量服务和现有 SQL/FTS 的职责与规模边界。
- Redis 若引入，只能承担有明确 TTL/失效/不可用语义的协调、限流、缓存或通知加速；持久业务事实、lease 终态、attempt 结算和审计仍以 PostgreSQL 为真源。

## 架构推论

1. 目标隔离主体不是单一 `kind`：Assistant 预算应以 `account_id` 为主，Steward 预算应以 `space_id` 为主，并保留 global、kind 和 control-plane 保留容量。
2. sidecar 的进程级并发和 backend 的进程级 worker/DB 资源都属于共享故障域；逻辑 token/scope 隔离不等于资源隔离。
3. control-plane 请求（heartbeat/lease/settle/cancel/health）必须有独立或保留容量，不能与 model stream/tool/RAG/index 共用可耗尽队列。
4. PostgreSQL 是更适合作为第一阶段持久事实、租约协调和并发事务真源的候选；Redis 作为加速层可后置；pgvector-first 能减少第二个持久事实系统和迁移面。
5. 多用户验收必须测 account×space 矩阵和故障注入，而不只是单个 run 成功。

## 仍需研究的问题

- 现有所有 `BEGIN IMMEDIATE`、SQLite-specific SQL/constraint、迁移和测试中哪些必须改为 PostgreSQL 行锁/advisory lock/`SKIP LOCKED`。
- scheduler/lease 在 PostgreSQL 下使用哪种公平选择：`FOR UPDATE SKIP LOCKED`、显式 ready queue、或持久化队列表 + tenant credits。
- Assistant 与 Steward 是否先同进程分池，还是直接拆成两个 sidecar deployment；控制面服务是否独立 worker。
- Redis 是否只做 admission/token bucket，还是还需要 pub/sub、短期 queue；Redis 不可用时的降级行为。
- pgvector 的数据量、embedding 更新、删除、visibility filter 和 ANN/过滤组合是否满足第一阶段；何时需要独立向量数据库。
- SQLite→PostgreSQL 的历史导入、双读/双写、校验、切换和回滚是否拆成独立子任务。
