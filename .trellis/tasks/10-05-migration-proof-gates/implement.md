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
- [ ] 其余 raw SQL 在两个数据库真实执行；不能只做 compile。
- [ ] 记录排序、时间、JSON 类型、整数/布尔、空值和错误码差异。
- [ ] 完成 RAG source/revision/scope/visibility/citation 与 lexical/vector index 的边界卡。

退出门：inventory 无未解释条目；每个差异都有保留、适配或拒绝结论。

## Phase 2：事务 / 锁 / 并发证明

- [x] 对全部真实事务入口建立 `TX-*` 合同卡：**65 个**（20 command_transaction + 22 _immediate_tx + 23 write_transaction），helper 定义已排除。计数对账见 `research/evidence/tx-entry-count-reconciliation.md`；清单见 `tx-entries.json`。
- [ ] 将每个入口归入 CAS、父行锁、advisory lock、counter、SERIALIZABLE+bounded retry 之一。
- [x] 锁序分析完成首轮：现有代码无 counter 锁，但 `_settle → fence_execution → acquire_run_writer` 与租约的 `counter → attempt` 构成反向；真实 PostgreSQL 探针已复现 `DeadlockDetected`。证据：`research/evidence/lock-order-analysis.md`。
- [ ] 双连接死锁探针：正确锁序通过，反向锁序必须出现可控冲突并被测试捕获。
- [ ] 验证 lease、counter、event seq、双 settle、cancel/settle、recovery、membership revoke。
- [ ] 对关键保护做 mutation：删 CAS、删锁、删 counter release、放宽谓词，测试必须失败。

退出门：所有关键合同至少有正向、负向、mutation 和 L3 证据。

## Phase 3：最小 PostgreSQL control prototype

- [ ] 建立仅用于隔离测试的 control schema prototype；不接业务 writer。
- [ ] 验证 schema build、constraints、indexes、refusal guard、重复执行和中断恢复。
- [ ] 验证多连接租约、续租、取消、settle、recovery、审计 exactly-once。
- [ ] 验证 process crash/connection loss/deadlock/serialization failure 的 bounded retry 和终态收敛。

禁止：RAG 生产检索实现、真实历史导入、writer 切换、开发部署。

## Phase 4：Snapshot / import / reconciliation / restore

- [ ] 定义隔离 SQLite snapshot 来源证明，禁止复制 live 主库。
- [ ] staging import 保留 ID、时间、状态、revision、FK 和 sequence。
- [ ] 对账 row count、hash、scope、status、run↔attempt、lease、egress、citation、RAG revision。
- [ ] 失败即 refusal：不自动修数据、不切 writer；重复导入可安全重试。
- [ ] 完成 backup/restore rehearsal，记录 RPO/RTO 和恢复后的 sequence/constraint/lease 状态。

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

## 当前阻塞

`10-05-migration-proof-gates` 仍在 planning。完成本计划并获得用户对最终规划摘要的明确批准后，才允许 `task.py start`；在此之前不 dispatch implement/check，不改业务代码。
