# Design — Steward 生产调度与运维可靠性

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 执行与数据合同

保留 canonical `StewardJob` 和 API 进程内 maintenance，独立 worker 进程不是本次必须项。
建议新增 `StewardSpaceSchedule(space_id PK, next_scan_at, last_scheduled_cursor, policy_version)`；用短 BEGIN IMMEDIATE 选择最多 10 个到期空间并更新调度时间，跨进程 CAS 防重复。
初始默认：扫描间隔 300 秒、单 tick core 最多 10 jobs、两次重试分别退避 5/30 秒、max_attempts 沿用 3，全部配置有正数和上界校验。扫描结果只触发执行，不能仅依据 max(event.id) 就宣告已经重算。

`StewardJob` 增 `available_at`、`retry_of_job_id`（可空逻辑关联）、必要的安全错误分类；保留 queued/leased/running/succeeded/failed/expired 词表。临时失败在预算内可回 queued；终态只能产生新的重跑行。core attempt 每次 lease 增加，模型辅助有独立阶段预算（见 assist-execution）。

接收事件时保存请求水位；lease 时固定本次处理上界。运行期间收到更高水位只请求后继工作，不能让结算把尚未处理的水位宣告完成。policy_version 不匹配时触发新策略重算并安全退役旧工作。

## 新增运维 API（拟定合同）

新建 `backend/app/api/admin_steward.py`，仅注册到 admin_app，复用 admin_deps/admin_audit。不能挂到已有 admin_agent router 的 AGENT_RUNTIME_ENABLED 门禁下：即使 Assistant runtime 或 Steward worker 关闭，管理员也应能读取 disabled 状态。重跑受 STEWARD_ENABLED 门禁，读状态不受引擎启用门禁。

- `GET /admin-api/v1/steward/status`：有效开关、worker heartbeat、队列计数、最老等待秒数、最近安全错误码。
- `GET /admin-api/v1/steward/jobs?space_id=&status=&page=&page_size=`：只返回 job_id/space_id/cause/status/attempt/可执行时间/安全错误码；page_size 默认 20、最大 100。
- `POST /admin-api/v1/steward/spaces/{space_id}/rerun`：`{reason, expected_policy_version}` + Idempotency-Key，返回 202 `{job_id, coalesced}`。默认每空间 60 秒冷却，可配置。
- family token→admin 401；unknown/inaccessible space 同一安全拒绝；策略冲突 409；过频 429；disabled 503。明确更新现有后台路由数量断言，绝不重挂旧 admin router。

## 故障与退出

停止时不再领取新 core job，等待当前短事务收敛；模型请求取消由 assist-execution 负责。租约恢复以 DB 为准，不能靠进程内布尔值假定所有工作都完成。已删除空间依外键/状态跳过，不能重新创建空间。

## 迁移及回滚

从实施时 migration head 增加新迁移，不预占 0035（工作区已有空间关联迁移）。空库与有旧 StewardJob 的库都需往返验证；停止 worker 后才回滚新增调度字段。关闭扫描/worker 保留事件与历史，重新开启执行有界追补。
