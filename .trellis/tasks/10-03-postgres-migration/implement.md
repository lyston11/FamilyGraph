# 实施计划：PostgreSQL 主存储与租约协调迁移

> 执行方式已重置：先完成 `10-05-migration-proof-gates`，再进入 Phase B 业务实现。未通过 Gate 的探针或测试不得被当作迁移完成证据。

## Phase 0：执行前证明门（硬前置）

- [ ] 完成 `10-05-migration-proof-gates` 的 PRD/design/implement 校验。
- [ ] 固化 inventory ID、环境 manifest、证据等级和当前失败/未决风险。
- [ ] 逐项检查**真实扫描出的 46 个**事务调用点（见 `10-05` 的 `tx-inventory.json`；
      早前记录的 18/43 是不同统计口径，数量只作索引，以 ID 清单为准）的锁参与者、锁序、
      CAS/counter 语义和调用方。
- [ ] 为每个关键不变量建立正向、负向、mutation、故障注入和回滚用例。
- [ ] 只有 Phase 0 全部通过后，才允许修改 Phase B 业务实现。

## Phase A：盘点与实验

- [x] 扫描 SQLite-specific SQL、`BEGIN IMMEDIATE`、PRAGMA、FK/CHECK/partial index、
      Alembic downgrade 和并发 fixture。结果见
      `research/evidence/compatibility-inventory.md`（18 处 BEGIN IMMEDIATE / 12 文件、
      11 处 json_extract / 4 文件、FTS5 虚拟表、sqlite3 驱动 5 文件）。
- [x] 启动隔离 PostgreSQL（`postgres:16-alpine`，独立端口与库），实测租约语义。
- [x] 建立租约原型并**证伪朴素移植**：`SKIP LOCKED` + 计数子查询会静默违反每租户
      并发上限（实测 5/5 worker 领同一租户）；修正版用租户级 `pg_advisory_xact_lock`
      后上限生效，且跨租户不阻塞（0.009s）。
- [x] **识别阻塞项**：RAG 用 FTS5 `tokenize='trigram'`（为 CJK 选的），PostgreSQL
      无对等物；`pg_trgm`/`tsvector`/`pgroonga` 语义各不同，需要独立决策与检索质量
      对照基准，不能夹在 schema 迁移里替换。
- [x] 记录解决方案：`research/evidence/solution-decision.md`，确定 CAS/父行锁/advisory
      lock/counter row 分类、PGroonga-first 词法候选、Unicode n-gram 后备、PG baseline
      + 静态快照导入 + 拒绝式对账路线。
- [x] 逐处分类 `BEGIN IMMEDIATE` 调用点（当时口径 43 个；真实扫描为 46，见 `10-05`
      的 `tx-inventory.json`）：A 条件 UPDATE / B 租约+counter / C 锁父行 / D advisory lock。
      证据 `research/evidence/begin-immediate-classification.md`。
- [x] **修复局部唯一索引的跨方言退化**（阻塞项）：16 个索引曾只有 `sqlite_where`，
      在 PostgreSQL 上退化为全表唯一索引（如 `UNIQUE(session_id)` = 一个 session 一生
      只能有一行）。改用 `app/models/indexes.py::partial_unique_index()` 统一两方言谓词；
      结构性回归 + 真实 PostgreSQL 语义验证 + 变异验证。
      证据 `research/evidence/partial-index-portability.md`。
- [x] **验证 schema 在 PostgreSQL 上可建**：逐表实建，87 张表中 84 张直接成功；
      唯一阻塞是 `json_extract`（已修，修后 87/87）。因此**不需要重写全部历史 Alembic**。
      证据 `research/evidence/pg-schema-feasibility.md`。
- [x] **运行期 SQL 可移植性**：11 处 `func.json_extract` 会在 PG 上**执行时**失败
      （不是建表失败，更隐蔽）。改用 SQLAlchemy 的 JSON 索引形式（JSON 列）与
      `json_text_field`（Text 列）；结构性回归 + 真实 PG 执行 + 变异验证。
      证据 `research/evidence/runtime-sql-portability.md`。
- [x] **验证 PostgreSQL 行锁语义**：`acquire_run_writer` 的 no-op UPDATE 在 PG 上确实
      串行化（8 并发无冲突；反证：不取锁则 7 次冲突）。
- [x] **发现并记录锁序死锁**：租约（counter→run）与结算（run→counter）交叉会真实死锁。
      固定顺序 `global → kind → tenant counter → run → attempt`；结算必须在 fence 前
      归还 counter。证据 `research/evidence/lock-order-deadlock.md`。
- [ ] 建立完整 agent/RAG schema prototype 的**其余部分**：约束/索引/触发器逐条语义审查。

## Phase B：控制层 schema

- [x] 验证租约配额形态：持久化 `counters` 行 + `SKIP LOCKED` 取候选。12 并发 worker
      下每空间上限真正生效（对比朴素移植的 5/5 越限），且无候选路径不泄漏名额。
      证据 `research/evidence/lease-counter-prototype.md`。
- [ ] 验证四条归还路径（settle / cancel / 租约过期恢复 / 栅栏退休）各自归还且不重复归还。
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
