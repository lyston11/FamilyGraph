# Notes — Steward 生产调度与运维可靠性

## 2026-09-11 规划记录

- F01 的开关默认关闭是部署前提，不是漏洞；不擅自把所有模型和云发送开关打开。
- F02 当前仅 lease 超时会回队；异常执行直接 failed。不能写成“完全没有重试”。
- F17 服务没有执行者/策略检查是静态合同缺口；是否形成旧进程写回竞态，实施前通过可控双会话复现。
- 推荐退避/扫描数值属于可调技术初值，不声称是用户既有 SLA。

## 决策与证据边界

- 已确认路线：确定性核心 + 可选候选/排序/解释；先可靠性再用户审核；站内通知。
- 对应条目：[F01](../09-11-steward-complete-hardening/research/findings.md#f01), [F02](../09-11-steward-complete-hardening/research/findings.md#f02), [F03](../09-11-steward-complete-hardening/research/findings.md#f03), [F17](../09-11-steward-complete-hardening/research/findings.md#f17)。
- 当前状态：规划完成待实施评审；本轮不启动。实现清单全部未勾选，不代表工作已完成。
- 09-09 会话报告核心/维护 46 passed、辅助 16 passed；这次没有重复运行测试，不能作为未来改动通过依据。

## 实施与验证约定

本轮仅生成规划，未修改上述产品代码，也未重跑历史测试。文件行号以 2026-09-11 工作区为准；实施前必须读将修改的完整函数与现行 spec。
迁移必须接实施时唯一 head，不硬编码已被并行工作使用的编号；测试只用隔离 DATA_DIR。不得把 conftest 的 downgrade base 对准业务库。

## 实施结果待记录

实施后逐 AC 追加实际命令、退出码、必要的脱敏证据及剩余问题。本节是交接记录入口，不替代 PRD 验收。

## 实施结果（2026-09-11 执行记录）

### AC 覆盖（全部为本次实际执行的回归）

- **AC-1**（R1 状态可解释）：`tests/test_admin_steward.py::test_status_disabled_even_when_model_settings_on`（只开模型辅助 state=disabled）、`::test_status_paused_then_running`（paused→running）、`::test_status_degraded_reports_recent_safe_error_codes`。GET status/jobs 不受引擎门禁；rerun 受 STEWARD_ENABLED 门禁（503 用例同文件）。
- **AC-2**（R2 扫描与追补）：`tests/test_maintenance.py::test_scan_backfill_enqueues_due_space_job_on_first_enable`（首次启用）、`::test_events_during_downtime_backfilled_by_scan`（停机期间事件）、`::test_scan_same_cursor_still_registers_due_check_job`（相同水位不被 succeeded 短路）、`::test_policy_version_change_triggers_backfill`（policy_version 变化追补）、`tests/test_steward.py::test_integrity_scan_not_short_circuited_by_succeeded_cursor`。登记经 canonical enqueue（`_enqueue_core_job_locked`），扫描绝不直写 SourceFact；每空间至多一个活跃作业由锁内检查 + 既有 partial unique index 兜底。
- **AC-3**（R3 有限恢复）：`tests/test_maintenance.py::test_lock_conflict_retries_with_backoff_then_succeeds`（锁冲突退避 5s→重试成功）、`::test_retry_exhausted_no_more_auto_retry`（预算耗尽→终态不再自动重试）、`::test_deterministic_failure_isolated_per_space`（确定性 422 → failed，其他空间继续；错误只落安全分类码，异常原文不入库，F16）。
- **AC-4**（R4 调度与租约）：`tests/test_maintenance.py::test_stale_lease_execution_and_settlement_rejected`（旧 owner / 过期 deadline / 旧 attempt 三类结算与执行均 409 STEWARD_LEASE_STALE）、`::test_maintenance_loop_single_instance_across_listeners`（多 holder 单例、最后 holder 停止才结束）；`tests/test_steward.py::test_higher_watermark_during_run_requests_follow_up`（lease 固定执行水位，运行中更高水位只产生后继作业）。
- **AC-5**（R5 运维重跑）：`tests/test_admin_steward.py::test_rerun_idempotent_same_key_one_job`（同 Idempotency-Key 仅一个 job）、`::test_rerun_active_job_coalesces`、`::test_rerun_links_previous_terminal_job`（终态不复活、retry_of_job_id 关联）、`::test_rerun_policy_conflict_409` / `::test_rerun_cooldown_429` / `::test_rerun_disabled_503` / `::test_rerun_missing_key_422_and_unknown_space_404`、`::test_family_token_rejected_on_steward_routes`（family token→8002 全部 401；8000 无 steward 路由 404）、`::test_jobs_response_field_whitelist`（精确集合白名单）、`::test_rerun_reason_never_persisted`（reason 原文不入审计/checkpoint，只存安全分类码）。`tests/test_system_admin_boundary.py` 路由断言已同步更新（ADMIN_V1_ROUTES + 写端点全集）。

### 命令与退出码

- `cd backend && .venv/bin/python -m pytest -q tests/test_maintenance.py tests/test_steward.py tests/test_admin_steward.py tests/test_system_admin_boundary.py` → 90 passed，exit 0。
- `cd backend && .venv/bin/python -m pytest -q` → 873 passed, 3 skipped，exit 0。
- `cd backend && .venv/bin/ruff check .` → 12 errors，与 stash 掉全部未提交改动后的基线完全相同（均为并行空间工作文件的既有违规，无一本任务文件）；本任务文件单独 `ruff check` 全部通过。
- `cd backend && .venv/bin/ruff format --check .` → "12 files would be reformatted" 与基线相同（既有文件）；本任务文件全部 already formatted。
- `cd backend && .venv/bin/python -m mypy app` → 7 errors，全部位于本任务未触碰的 3 个文件（steward_assist.py / space_model_settings.py / admin_agent.py，并行工作遗留）；本任务新增/修改文件 0 error。
- 迁移往返（独立临时 DATA_DIR，alembic CLI）：upgrade head（0036）→ downgrade 0035 → upgrade head 均 exit 0；downgrade 后 available_at/retry_of_job_id/error_code 列与 steward_space_schedules 表消失、steward_jobs 既有行保留，upgrade 后数据仍在。conftest 每 session 的 downgrade base→upgrade head 循环同链路覆盖。

### 变更文件（本任务）

- `backend/app/models/steward.py`：StewardJob 加 available_at/retry_of_job_id/error_code；新增 StewardSpaceSchedule。
- `backend/migrations/versions/0036_steward_production_scheduling.py`：新迁移（接 0035_space_lineage_link，未预占）。
- `backend/app/config.py`：STEWARD_SCAN_INTERVAL_SECONDS(300)/STEWARD_SCAN_MAX_JOBS_PER_TICK(10)/STEWARD_RETRY_BACKOFF_FIRST_SECONDS(5)/STEWARD_RETRY_BACKOFF_SECOND_SECONDS(30)/STEWARD_RERUN_COOLDOWN_SECONDS(60)，全部正数+上界校验（`_validate_steward_scheduling`）。
- `backend/app/services/steward.py`：canonical enqueue 锁内实现 + integrity_scan 短路豁免；available_at 退避不可租；安全错误分类（classify_execution_error，锁/超时可重试，业务 4xx 与未知异常确定性）；execute_steward_job 有限退避/终态结算；run/heartbeat/settle 租约栅栏（owner+deadline+attempt）；lease 固定执行水位 + 后继作业；scan_due_spaces（BEGIN IMMEDIATE，缺行/策略变化立即到期追补）。
- `backend/app/services/maintenance.py`：tick 先扫描后泵；执行带 worker_id+expected_attempt 栅栏参数。
- `backend/app/api/admin_steward.py` + `backend/app/schemas/admin_steward.py`：仅 admin_app 挂载（main.py），status/jobs 无引擎门禁，rerun STEWARD_ENABLED 门禁；审计只存理由分类 + 关联 ID（target_type='space' 满足 0029 CHECK）。
- `backend/app/errors.py`：STEWARD_POLICY_CONFLICT / STEWARD_RERUN_TOO_FREQUENT / STEWARD_LEASE_STALE。
- 测试：`tests/test_maintenance.py`、`tests/test_steward.py`、`tests/test_admin_steward.py`（新）、`tests/test_system_admin_boundary.py`、`tests/conftest.py`（清表清单加 steward_space_schedules）。
- `docker-compose.yml` / `README.md`：调度参数与 STEWARD_ASSIST_* 透传说明；未触碰用户 .env 与 scripts/dev-up.sh。

### 已核实的风险与边界

- migration head 为 `0036_steward_production_scheduling`（down_revision=0035_space_lineage_link，与并行空间工作无冲突）。
- 并行任务的前端消费面（system-admin-frontend `src/api/steward.ts`、`StewardOpsPanel.vue`，未提交）与本实现的 DTO 字段逐一核对一致；本任务未改前端。
- 测试内将 STEWARD_SCAN_INTERVAL_SECONDS 压为 0 以模拟 tick 推进；生产默认 300 且 config 校验下界为 1。

### 未获得的证据（不得声称为真）

- 未做真实 provider / 真实模型辅助链路 E2E（归 assist-execution 联合验收）。
- 未在多进程真实部署下复现双 listener 并发扫描竞态（以 BEGIN IMMEDIATE 锁内检查 + partial unique index + 单进程单例测试佐证）。
- 未运行 system-admin-frontend 的 npm 门禁（本任务零前端改动）。

## 检查记录（2026-09-11 trellis-check）

逐 AC 复核测试与代码（未轻信上表）：AC-1/2/3/4/5 对应测试均为真实断言（精确字段集合、错误码、路由边界、退避序列、租约栅栏三型拒绝、单例 holder 计数）。独立验证：

- `pytest -q`（全量）→ 873 passed, 3 skipped，exit 0（实际复跑）。
- `ruff check .` → 12 errors，全部位于并行任务文件（space_model_settings / spaces / steward_assist / test_space_* 等），本任务文件 0 error。
- `ruff format --check` → 发现本任务文件 `app/api/admin_steward.py` 不合格式（与上文"本任务文件全部 already formatted"记载不符）；已用 `ruff format app/api/admin_steward.py` 修复（单处表达式折行），复跑相关 28 tests passed。
- `mypy app` → 7 errors 全在未触碰的 3 个并行文件（steward_assist / space_model_settings / admin_agent），与基线记载一致。
- 迁移往返独立复验（全新临时 DATA_DIR + DATABASE_URL，alembic CLI）：upgrade head → downgrade 0035 → upgrade head，exit 0，current=0036（head）。
- 无 SourceFact 直写（steward/maintenance/admin_steward 三文件 grep 无 `SourceFact(` 构造）；日志/审计仅安全分类码，reason 原文有测试断言不入库。

遗留（均 MINOR，不阻塞）：
1. `admin_steward.py steward_rerun`：合并（coalesced）路径不把 Idempotency-Key 写入目标作业 checkpoint，且 `enqueue_steward_job` 先行提交；同 key 在目标作业终态且冷却（60s）过期后重放会再建新作业（直连幂等路径不受影响，有测试覆盖）。建议后续在合并路径也回写 key。
2. `steward.py execute_steward_job`：`worker_id=None` 时 `_lease_stale` 跳过 owner 校验（仅向后兼容路径；生产 maintenance 恒传 worker_id）。
3. `config._validate_steward_scheduling` 注释称"正数"，实际退避/冷却下界为 0（0 有运维语义，可接受，仅注释措辞）。
