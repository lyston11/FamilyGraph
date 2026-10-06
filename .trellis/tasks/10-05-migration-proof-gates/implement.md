# 实施计划：迁移执行前证明与安全门

## Phase 0：冻结与基线

- [x] 确认当前主检出干净；业务代码只能在任务 worktree 修改。
- [x] 建立环境 manifest：Python/SQLAlchemy/Alembic/PostgreSQL/Docker 版本、DATABASE_URL 目标、隔离端口和数据目录。证据：`research/evidence/environment-manifest.json`。
- [x] 将已有发现写入风险登记，标记 L0-L3 证据等级；没有 L4 前不得声称开发可用。
- [x] 固定父任务与 5 个架构子任务的边界、依赖和阻塞关系。

验证：`task.py current --source`、`git status --short`、`task.py validate`。

## Phase 1：Schema / SQL / dialect inventory

- [x] 为每张表、列、FK、CHECK、unique/partial index、trigger、raw SQL、migration、backup/export 路径生成 stable ID。自动扫描产物：`research/evidence/gate-1-inventory.{json,md}`；当前仅为 L0 inventory。
- [ ] 逐项比较 SQLite/PostgreSQL DDL render，特别是 `sqlite_where`/`postgresql_where`、JSON CHECK、NULL、FK action 和 partial unique index。源码 stable inventory 已生成，方言执行矩阵仍未通过。
- [x] 三条历史迁移的 SQLite 专属构造已在真实 PostgreSQL 上确认失败（`0042` json_extract / `0022` last_insert_rowid / `0014` FTS5），并含方言感知修复的反证。证据：`research/evidence/migration-replay-blockers.md`（L2）。
- [x] raw SQL 已结构化并按风险分类（`raw-sql-inventory.json/md`）：6 处 SQLite-only 函数、31 PRAGMA、23 SQLite DDL、3 BEGIN IMMEDIATE、0 字面量 AUTOINCREMENT；方言中立 1475 处仅计数。**已排除两类误报**（`autoincrement=True` 实测可移植、`strftime` 是 Python 方法）。
- [ ] 其余 raw SQL 在两个数据库真实执行：目前只有 FTS5、2 个触发器、3 条运行期查询实测，PRAGMA 与其余 SQLite DDL 未逐条验证。
- [ ] 记录排序、时间、JSON 类型、整数/布尔、空值和错误码差异。
- [ ] 完成 RAG source/revision/scope/visibility/citation 与 lexical/vector index 的边界卡。

退出门：inventory 无未解释条目；每个差异都有保留、适配或拒绝结论。

## Phase 2：事务 / 锁 / 并发证明

- [x] 对全部真实事务入口建立 `TX-*` 合同卡：**65 个**（20 command_transaction + 22 _immediate_tx + 23 write_transaction），helper 定义已排除。计数对账见 `research/evidence/tx-entry-count-reconciliation.md`；清单见 `tx-entries.json`。
- [x] 将全部 65 个入口归入 CAS(A=8)、父/行锁(C=50)、advisory+唯一约束(D=4)、counter(B=3)。分类表以 `(path, function)` 为键，未分类即脚本失败。证据：`research/evidence/tx-contracts.json`。
- [x] 锁序分析完成首轮：现有代码无 counter 锁，但 `_settle → fence_execution → acquire_run_writer` 与租约的 `counter → attempt` 构成反向；真实 PostgreSQL 探针已复现 `DeadlockDetected`。证据：`research/evidence/lock-order-analysis.md`。
- [ ] 双连接死锁探针：正确锁序通过，反向锁序必须出现可控冲突并被测试捕获。
- [ ] 验证 lease、counter、event seq、双 settle、cancel/settle、recovery、membership revoke。
- [ ] 对关键保护做 mutation（**部分完成**：入口计数 mutation 已实测；针对真实业务入口的删 CAS/删锁/删 counter release 尚未补）。

退出门：所有关键合同至少有正向、负向、mutation 和 L3 证据。

## Phase 3：最小 PostgreSQL control prototype

- [x] 建立隔离 control schema prototype 与**四类触发器 plpgsql 等价物**（scope-immutable / append-only / conditional-immutable / sticky-status / revision-counter），含负向用例与反证。证据：`research/evidence/gate-3-baseline-prototype.md`（L2）。**未覆盖**：69 个对象的逐条等价物（60 个 `sri_*` 只验证了一类行为）、`rag_*` 触发器具体语义、列级 `UPDATE OF` 写法。
- [ ] 验证 schema build、constraints、indexes、refusal guard、重复执行和中断恢复。
- [ ] 验证多连接租约、续租、取消、settle、recovery、审计 exactly-once。
- [x] 故障注入六类：提交前/后断连、重复 settle、cancel vs settle、崩溃租约回收、SERIALIZABLE 冲突，全部按预期收敛（含 counter 恰好归还一次）。证据：`research/evidence/gate-4-fault-injection.md`（**原型 L3**）。**未覆盖**：真实业务 schema、membership revoke、after-upstream-sent、真实进程 kill、deadlock_timeout 延迟。

禁止：RAG 生产检索实现、真实历史导入、writer 切换、开发部署。

## Phase 4：Snapshot / import / reconciliation / restore

- [ ] 定义隔离 SQLite snapshot 来源证明，禁止复制 live 主库。
- [x] staging import 机制验证：静态快照（`Connection.backup()`）→ 逐表保留原始 ID/FK → 行数与逐行摘要对账 → 重复导入幂等。证据：`research/evidence/gate-5-import-reconcile.md`（原型）。**sequence 未真正验证**（合成表 PK 非 serial）。
- [ ] 对账 row count、hash、scope、status、run↔attempt、lease、egress、citation、RAG revision。
- [x] refusal 语义实测：人为制造差异后被检出，且**未自动修复**（差异保留）。重复导入幂等。
- [ ] 完成 backup/restore rehearsal（**未开始**：`pg_dump`/`pg_restore`、RPO/RTO、sequence/constraint/lease 恢复状态）。

## Phase 5：跨任务接缝

- [ ] `control-plane-fault-domain` 提供 control reserve、worker recovery 和 health 接口。
- [ ] `provider-reliability-boundaries` 提供 stream quota/circuit，不破坏 retry/egress/settle。
- [ ] `postgres-operations-cutover` 提供连接预算、backup/PITR/HA、writer epoch 和回滚 runbook。
- [ ] `lexical-search-migration` / `pgvector-rag` 提供检索质量与索引生命周期证据。
- [ ] `multitenant-load-acceptance` 提供最终 account×space×kind 故障矩阵。

任何接缝未 verified，父任务不得进入 writer cutover。

## Phase 6：开发灰度准备

- [ ] 只在所有前置 Gate 通过后做 shadow read。
- [ ] 按 `shadow → control writer → agent writer → domain writer` 顺序切换。
- [ ] 每阶段观察 control p95/p99、queue wait、DB wait、retry、lease、audit、scope/hash 对账。
- [ ] 失败只回滚路由/epoch，不双主、不手工修改两边状态。
- [ ] 线上不自动操作；只交付人工发布与回滚步骤。

## 固定切片检查单

```text
[ ] PRD/design/contract card 已更新
[ ] 调用方和真实数据流已核对
[ ] SQLite/PG 真实执行矩阵通过
[ ] 正向用例通过
[ ] 负向用例通过
[ ] mutation 能捕获保护删除
[ ] 两连接或多连接并发通过
[ ] 故障注入与恢复通过
[ ] rollback/refusal 可执行
[ ] 未覆盖项和证据等级已登记
[ ] 代码、证据、worktree、提交一致
```

## 当前状态与阻塞

任务已 `in_progress`（Gate 0 通过；Gate 1/2 进行中）。当前阻塞：

- **Gate 1 BLOCKED**：raw SQL 与 backup 路径仍只有正则命中，未结构化、未在两种数据库真实执行；index/constraint/trigger 已补 owner/status/evidence，但 raw SQL 尚未。
- **Gate 2 BLOCKED**：缺静态调用图（当前只覆盖 `_settle`/`settle_attempt` 一条路径）；三把以上锁未实测；mutation 用例待补。
- **Gate 3-7 未开始**：PostgreSQL baseline prototype、故障注入、导入对账、跨任务接缝、最终矩阵。

已完成并可用：65 个事务入口分类（含强制机制与 mutation 验证）、方言阻塞探针、死锁探针、14 个触发器阻塞证据、环境 manifest。

**不得**据此放行 `postgres-migration` 的业务实现。
