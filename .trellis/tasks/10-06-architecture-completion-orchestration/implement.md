# 实施计划：多租户架构全量完成与连续执行编排

## Phase 0：启动前

- [x] 读取本任务 `implement.jsonl` / `check.jsonl` / `prd.md` / `design.md`。
- [x] 确认 main 干净、任务 worktree、branch、base commit 和环境 manifest。
- [x] 执行 `task.py validate`；确认所有子任务 PRD/design/implement 存在且依赖写入各自工件。
- [x] 建立 `execution-ledger.md`：每个 Gate、owner、输入、输出、验证、commit、阻塞和下一命令。含 ORCH-10 无主 TBD 清单。
- [x] 启动后不再向用户询问下一步；按本文件继续。

## Phase A：PostgreSQL baseline / transaction

### A1 Schema baseline

- [ ] 生成 88 表、979 列、112 index、105 constraint、69 trigger 的逐项 baseline map。
- [ ] 为每个 SQLite trigger 写 PostgreSQL 等价函数/trigger，并逐项负向验证。
- [ ] 将 partial index、JSON CHECK、runtime JSON、FK/ON DELETE、sequence 和 refusal guard 纳入 baseline。
- [ ] 用空隔离 PostgreSQL 执行 baseline upgrade/repeat/interruption/refusal。

### A2 Counter / lease / CAS

- [ ] 创建 global/kind/account/space/provider counter schema。
- [ ] 实现 canonical lock order、capacity check、candidate SKIP LOCKED、lease generation。
- [ ] 实现 settle/cancel/recovery/revoke exactly-once release。
- [ ] 将真实 `lease_next`、`lease_next_steward_job`、`lease_attempt` 接入 counter。
- [ ] 在真实业务 schema 上重跑 lock graph、三锁探针、CAS/lock/release mutation。
- [ ] 真实 PostgreSQL 多连接通过后才标记 A 完成。

### A3 Import / restore

- [ ] 静态 snapshot provenance。
- [ ] staging import、ID/FK/revision/timestamp 保留。
- [ ] sequence repair、row/hash/scope/status/run-attempt/lease/egress/RAG reconciliation。
- [ ] pg_dump/pg_restore、PITR/RPO/RTO（由 operations 接缝提供）演练。
- [ ] mismatch refusal、重复导入、断点重试、恢复后 constraint/lease 校验。

## Phase B：Control-plane fault domain

- [ ] 独立 control worker pool、DB connection reserve、health/heartbeat/lease priority。
- [ ] Assistant account pool 与 Steward space pool 分离。
- [ ] background/RAG pool 不得占用 control reserve。
- [ ] sidecar 重启、lease expiry、worker recovery、membership revoke 收敛。
- [ ] control starvation mutation：移除 reserve 后矩阵必须失败。

## Phase C：Provider reliability

- [ ] stream quota、connect/header/stream deadline、backpressure。
- [ ] upstream circuit breaker、half-open、provider/kind isolation。
- [ ] run retry budget 与 session/request retry 不相乘失控。
- [ ] cancel/lease lost 流中断、egress exactly-once、永久 4xx 不重试。
- [ ] Provider fault injection 与恢复矩阵。

## Phase D：Redis coordination

- [ ] admission/token bucket/wakeup/cache key contract。
- [ ] Redis outage/latency/restart/eviction/failover matrix。
- [ ] PostgreSQL fallback 或 fail-closed；禁止 Redis 变成 lease/settle 真源。
- [ ] 双实例 Redis admission mutation 与持久 counter 对账。

## Phase E：Lexical / vector RAG

- [ ] PGroonga lexical index、Unicode n-gram fallback、index version metadata。
- [ ] pgvector embedding/source/revision/scope/visibility/citation。
- [ ] filter-then-search、final authorization、撤权/删除/版本切换/rebuild。
- [ ] embedding failure、index corruption、maintenance lease loss、RAG disabled。
- [ ] golden corpus recall/precision/latency/index-size/write-cost。

## Phase F：Operations / cutover

- [ ] connection budget、PgBouncer、pool reserve、long transaction policy。
- [ ] WAL/archive/PITR/HA/failover、backup/restore、RPO/RTO。
- [ ] writer epoch/readiness/migration health/refusal。
- [ ] shadow → control writer → agent/domain writer。
- [ ] 失败按 epoch/route rollback，不双主、不手工修数据。

## Phase G：Final load acceptance

- [ ] account×space×kind×provider×control-plane×RAG 矩阵。
- [ ] 同用户跨空间、同空间多用户、Assistant/Steward 混跑。
- [ ] tool burst、长流、control reserve、连接耗尽、Redis outage、provider outage。
- [ ] cancel/settle/recovery/revoke/sidecar restart/backup restore/index rebuild。
- [ ] p50/p95/p99、queue wait、DB wait、lease、retry、audit、scope/hash reconciliation。
- [ ] 用户可见结果核对：终态、错误码、取消、授权遮罩、citation。

## 每个切片的连续执行协议

```text
1. 读取本切片 contract card
2. 读取调用方和现有测试
3. 写/更新正向测试
4. 写负向与 mutation
5. 实现最小改动
6. 运行受影响检查
7. 运行真实隔离依赖检查
8. 故障注入与恢复
9. 更新 evidence/risk/ledger
10. commit
11. 继续下一个未完成项
```

## 失败处理协议

- 实现 bug：修复后重跑当前 slice；
- oracle 错误：保留失败证据，更新 oracle 理由，再重跑；
- 环境阻塞：隔离、记录命令和恢复条件，执行无依赖 slice；
- 设计未决：按 `design.md` 默认决策落地；只有产品语义矛盾才暂停；
- 范围越界：创建/更新对应子任务，不把工作丢回用户；
- 任何失败不得删除断言、放宽阈值、换环境或手工改数据。

## Commit / branch / archive

- 每个任务一个 worktree/branch；业务代码只能在对应 worktree。
- 子 agent 不得 merge/push/reset/rebase。
- 主检出只做串行 merge；先合依赖再合编排任务。
- 任务完成前检查 worktree clean、证据与代码同一 commit、main 无污染。
- 只有所有 acceptance criteria、rollback、未覆盖项和跨任务接缝闭合后才 `task.py archive`。

## Final commands

```bash
python3 ./.trellis/scripts/task.py validate <task>
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . \
  && .venv/bin/mypy app && .venv/bin/pytest
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd frontend && npm run type-check && npm run lint && npm test && npm run build
python3 ./.trellis/scripts/task.py archive <task>
```

高成本检查不得被省略；若环境阻塞，记录 exit 2 和恢复条件，不得标记通过。
