# Agent Note: Steward 生产调度采用进程内空间调度表 + 数据库租约栅栏

Status: implemented

## Problem

Steward 此前只有"事件入队 + 泵 queued"的消费者：空闲空间到期卡、停机期间遗漏事件、重启追补没有生产者；执行异常一律进 failed 终态；run/heartbeat/settle 只检查状态字段，旧执行者可以用过期租约覆盖新租约的结果。多 listener 形态下这些缺口都会复现。

## Decision

迁移 0036 引入 `StewardSpaceSchedule(space_id PK, next_scan_at, last_scheduled_cursor, policy_version)`，调度保留在 API 进程内的 maintenance 循环（进程级单例 + holder 计数）：

- 每 tick 用 BEGIN IMMEDIATE 短事务选最多 10 个到期空间，经 canonical enqueue 合同登记作业（扫描路径对 `integrity_scan` 豁免 succeeded 光标短路）；首次启用、重新启用、policy_version 变化触发有界追补；
- `StewardJob` 增 `available_at`/`retry_of_job_id`/`error_code`：DB 锁等可重试错误按 5s/30s 退避回队（`STEWARD_MAX_ATTEMPTS` 默认 3），输入/权限类确定性错误直接 failed 且不影响其他空间；终态不复活，人工重跑创建关联新作业；
- run/heartbeat/settle 全部校验 `worker_id + expected_attempt + lease owner + deadline`，旧执行者结算被拒（`STEWARD_LEASE_STALE`）；lease 时固定执行水位 checkpoint，运行中更高水位只产生后继作业；
- 8002 管理面提供 status/jobs/rerun（Idempotency-Key 幂等、冷却 429、策略冲突 409、关闭 503），读状态不受引擎启用门禁。

## Alternatives considered

- **独立 worker 进程 + 外部队列（Redis 等）** — 故障域隔离更好，但当前容量与部署证据不支持新增常驻组件；本决定把调度合同设计成与执行位置无关，后续迁出时只换调度器实现。
- **进程内布尔值/内存锁标记"工作已完成"** — 多 listener 下必然漂移，崩溃后无法自愈；恢复语义必须以数据库行为准，故否决。
- **不做周期扫描，只靠事件驱动** — 已核实的到期卡、停机追补缺口无法覆盖，空闲空间永不收敛。

## Consequences

- **收益**：无论停机、失败还是重启，确定性结果都可收敛；同空间至多一个活跃 core job；扫描/事件/人工重跑合并水位不重复执行。
- **代价与已知上限**：扫描间隔（默认 300s）内的新事件依赖事件入队路径，扫描只是兜底；调度与执行同进程，API 进程死亡时 worker 一并停止——由观测面（degraded/queue_stalled 告警）暴露而非掩盖。

## Verification

`backend/tests/test_maintenance.py`、`tests/test_steward.py`（多 listener 单循环、租约栅栏三形态、退避/终态分类）；`scripts/steward_e2e.py` 的中断恢复与关闭重开追补场景。

Note: 扫描/重试/租约栅栏的合同入口 — 见 .agent-notes/implemented/architecture/2026-09-11-steward-scheduling-lease-fencing.md
