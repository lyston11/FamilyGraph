# 实施计划：PostgreSQL 运行运维与切换恢复

## Phase A：容量与连接

- [x] 计算每个 backend/worker/sidecar 实例的 control/execution/background/admin 连接预算。
      证据：`pg_connection_budget_probe.py` + `research/evidence/pg-connection-budget.md`；启动期由 `config._validate_agent_execution_admission` 强制 `AGENT_EXECUTION_GLOBAL_CAPACITY ≤ POOL_MAX_CONNECTIONS-1`
- [x] 在隔离 PostgreSQL 注入 pool exhaustion、长事务、断线、重启和 failover。
      2026-10-23 追加：**真实 PG 重启**（`docker restart fg-acc-pg`）与 **Redis 中断**
      已在专用验收实例上注入——重启期间恰好 1 次 500（连接中途断开），恢复后 60/60 全 200；
      Redis 停时控制面 60 次全 200、无挂死。见
      `research/evidence/restart-and-redis-2026-10-23.md`。
      证据：2026-10-23 在**专用验收实例**（`fg-acc-pg` 55460 + 第二 API 进程 8010）上
      实测（`pool_exhaustion_probe.py`，含端口白名单护栏）：
      - pool exhaustion：占住 **81/100** 条连接（`total=92`）→ `/api/ready`
        p99=18.23ms、全部 200、**零 5xx、零无响应**；
      - 长事务：持有未提交事务 20s（`idle in transaction: 1`）→ p99=17.62ms；
      - 断线：终止 5 条空闲连接后自愈，后续 60 次全部 200；
      - 释放后 `total` 回到 11，无泄漏。
      见 `research/evidence/pool-exhaustion-2026-10-23.md`。
      **failover 仍未做**（专用实例是单节点，无主备）——已在证据文件中单独登记。
- [x] 决定是否需要 PgBouncer，并验证 session state/prepare/notify 兼容性。
      证据：`pgbouncer_compat_probe.py` PASS：transaction pooling 下配额守恒、prepared statement 正常；失败模式已量化（部署二选一）

## Phase B：备份恢复

- [x] 配置隔离 base backup/WAL/PITR 实验。
      证据：`c7-pitr.md`：真实归档 + 恢复到目标 LSN + 负向断言
- [x] 设计 restore 后 lease recovery、writer epoch 和 readiness 检查。
      证据：`writer_epoch.migration_health` + `/api/ready` 报告 `writer_stage`/`writer_epoch`/`postgres_authoritative`
- [x] 验证 run/attempt/egress/RAG/citation 对账。
      证据：2026-10-23 `reconcile_real_schema.py` 7 维度无差异（含 egress 每次尝试一条、revision 镜像、attempt/run 无孤儿）

## Phase C：切换与保留

- [ ] 写开发环境冻结写入、静态导入、对账、切换、回滚 runbook。
- [ ] 设计 event/egress/audit/RAG index 的归档或分区，不破坏回放/审计。
- [ ] 故障注入后完成多用户并发和恢复矩阵。

## Gate

- [ ] RPO/RTO、最大连接数、writer epoch、备份恢复证据全部落盘；未通过不进入线上发布。

## 2026-10-23 复核

**关键更正**：`writer_state.stage` **不控制写路由**。选库只由 `DATABASE_URL` 决定
（`db.py:31 IS_SQLITE = config.DATABASE_URL.startswith("sqlite")`），`stage` 只影响
`/ready` 报告与 `capacity.assert_ready` 的 `pg_all` 门禁。因此「回滚」= 改
`DATABASE_URL` + `advance()` 递增 epoch，**不是** stage 变化触发的写重路由。
把 stage 当写路由会让「回退到 pg_control」意外停写。

**仍未做**：

| 项 | 为什么还开着 |
|---|---|
| HA / failover、RPO/RTO、多时间线 | 需要真实多实例；PRD 停止条件 |
| 写开发环境冻结写入、静态导入、对账、切换、回滚 runbook | 切换已实做（`c9-p0-p1-execution.md`），但**成文 runbook 未写** |
| event/egress/audit/RAG index 的归档或分区 | 未设计 |
| 故障注入后完成多用户并发和恢复矩阵 | 归 `10-04-multitenant-load-acceptance` |
| 线上发布 | **由用户手动执行**（AGENTS.md） |
