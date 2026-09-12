# Notes — Steward 模型调用事务隔离、预算与崩溃恢复

## 2026-09-11 规划记录

- F04/F05：`run_steward_job` BEGIN IMMEDIATE 包含 `_execute_locked`→`run_assists`→HTTP，SAVEPOINT 不会释放外层写锁。旧 design §3.3 声称“提交后”与实际 hook 矛盾。
- F06：当前 _budget_state 只统计 succeeded；invalid ranking 会改成 degraded，已耗 token 因此从累计消失。
- 部署新辅助阶段不是第二套产品 Agent/通用队列；是 canonical job 的受限子工作记录。

## 决策与证据边界

- 已确认路线：确定性核心 + 可选候选/排序/解释；先可靠性再用户审核；站内通知。
- 对应条目：[F04](../09-11-steward-complete-hardening/research/findings.md#f04), [F05](../09-11-steward-complete-hardening/research/findings.md#f05), [F06](../09-11-steward-complete-hardening/research/findings.md#f06), [F16](../09-11-steward-complete-hardening/research/findings.md#f16), [F19](../09-11-steward-complete-hardening/research/findings.md#f19)。
- 当前状态：规划完成待实施评审；本轮不启动。实现清单全部未勾选，不代表工作已完成。
- 09-09 会话报告核心/维护 46 passed、辅助 16 passed；这次没有重复运行测试，不能作为未来改动通过依据。

## 实施与验证约定

本轮仅生成规划，未修改上述产品代码，也未重跑历史测试。文件行号以 2026-09-11 工作区为准；实施前必须读将修改的完整函数与现行 spec。
迁移必须接实施时唯一 head，不硬编码已被并行工作使用的编号；测试只用隔离 DATA_DIR。不得把 conftest 的 downgrade base 对准业务库。

## 实施结果待记录

实施后逐 AC 追加实际命令、退出码、必要的脱敏证据及剩余问题。本节是交接记录入口，不替代 PRD 验收。

## 实施记录（2026-09-11 执行轮）

### AC 覆盖（全部有自动化回归，位于 backend/tests/test_steward_assist.py）

- AC-1（R1 事务隔离）：`test_slow_http_does_not_block_other_space_writes`——fake transport 挂起 HTTP 期间，第二个 SQLite 连接（独立 SessionLocal）成功提交领域事件并完成另一空间的确定性 core job；core 结果对第三连接已可读。`test_killed_assist_does_not_rollback_core`——辅助线程被杀死后 core 仍 succeeded，批次经恢复器按 unknown 收敛。
- AC-2（R2 四个崩溃点）：core 提交后（批次与 core 同事务原子；`recover_stuck_batches` 兜底补登孤儿批次）；发送前（`test_crash_point_2_before_send_recovers_to_pending`：预留释放、零计费、批次回 pending 可重调度）；发送后审计前（`test_crash_point_3_after_send_before_audit`：in_flight → unknown，按预留保守计费，批次 failed，不自动重发）；写回前（`test_crash_point_4_before_writeback_applies_after_fence`：succeeded+output_json 重跑栅栏后 CAS 应用）。Assistant 三表隔离沿用 `test_assist_never_touches_assistant_tables`。
- AC-3（R3 预算）：`test_budget_two_caps_three_kinds_at_two_sends`（预算 2、三类辅助 4 个主题、两次 ReadTimeout → 恰 2 次发送 + 2 skipped）；`test_insufficient_tokens_skips_without_send`；`test_conservative_billing`（参数化：total 有效 / 缺 total→input+output / 全缺→预留 / 负数回落 / 部分字段 / 0 视为无效）+ `test_usage_missing_billed_from_reservation`。
- AC-4（R4 写回栅栏）：调用期间经 after_send 钩子注入变化——关平台开关（assist_disabled）、换 provider/model（provider_changed）、卡片终态（card_changed）、源事实 revision 变化（evidence_changed）→ 全部不应用，批次 superseded 且安全原因码落 error_code。
- AC-5（R5 限量）：`test_oversized_prompt_skipped_without_send`；`test_oversized_response_capped_without_full_read`（流式字节上限，超限 failed/response_too_large）；`test_transport_receives_30s_default_timeout`（timeout 参数 = STEWARD_ASSIST_TIMEOUT_SECONDS 默认 30）；`test_lease_deadline_stops_followup_sends`（lease 墙钟耗尽 → 剩余 attempt 不发送）；跨空间 core 在 HTTP 在飞时照常完成（AC-1 用例内）。

### 实际执行命令与退出码

- `.venv/bin/python -m pytest -q tests/test_steward_assist.py tests/test_maintenance.py tests/test_steward.py` → 94 passed（exit 0）
- `.venv/bin/python -m pytest -q tests/`（backend 全量）→ 892 passed, 3 skipped（exit 0）
- `.venv/bin/ruff check app/models/steward.py app/services/steward_assist.py app/services/steward.py app/services/maintenance.py app/config.py tests/conftest.py tests/test_steward_assist.py tests/test_maintenance.py migrations/versions/0037_steward_assist_batches.py` → All checks passed（exit 0）
- `.venv/bin/ruff format --check`（同上任务文件）→ 9 files already formatted（exit 0）
- `.venv/bin/python -m mypy app` → 本任务新增/修改文件 0 错误；仅剩 app/api/space_model_settings.py 与 app/api/admin_agent.py 的 5 条基线错误（本任务未触碰这些文件，属既有/依赖任务未提交改动的基线）。
- 迁移往返：`DATA_DIR=$(mktemp -d) alembic upgrade head && downgrade 0036_steward_production_scheduling && upgrade head` → exit 0。

### 变更文件

- `backend/app/models/steward.py`：新增 `StewardAssistBatch`（job UNIQUE、fence_json 证据快照、独立 lease）；`StewardModelCall` 扩展为 attempt（batch_id/subject_key/input_hash/attempt_no/预留与计费列/output_json），状态 CHECK 扩展 reserved/in_flight/unknown。
- `backend/migrations/versions/0037_steward_assist_batches.py`：新表 + 加列 + uq_smc_attempt_key 唯一索引 + CHECK 替换（SQLite batch_alter）；可 downgrade。
- `backend/app/services/steward_assist.py`：重写为批次执行器——register（core 同事务、无网络）/ schedule_due_batch（预留+lease，全局并发上界）/ execute_batch（tx1 预发送栅栏 → 事务外 HTTP（字节上限流式读取、单次 timeout+lease 墙钟双上界）→ tx2 审计+保守计费 → tx3 写回栅栏+CAS）/ recover_stuck_batches（四崩溃点）；预算=调用数+token 预留（输入保守上界 1 token/byte、输出 cap=min(kind cap, 剩余)）；unknown 不自动重发（上游未证实支持幂等键）。
- `backend/app/services/steward.py`：`_execute_locked` 的 `run_assists` hook 改为 `register_batch_for_job`（同事务登记，无 HTTP）；修正模块头"SAVEPOINT 防回滚"旧说明为真实崩溃合同。
- `backend/app/services/maintenance.py`：tick 先泵 core，再 recover + schedule 至多一个辅助批次，HTTP 经 `launch_batch` 提交到有界 ThreadPoolExecutor（自有 Session，不阻塞 core tick）；停机 `shutdown_assist_executor`（不无限等待 httpx）。
- `backend/app/config.py`：新增 STEWARD_ASSIST_MAX_PROMPT_BYTES / MAX_RESPONSE_BYTES / BATCH_LEASE_SECONDS / MAX_CONCURRENT_BATCHES（含 ensure_ready 区间校验）。
- `backend/tests/conftest.py`：steward_assist_batches 加入清表顺序（先于 steward_model_calls）。
- `backend/tests/test_steward_assist.py` / `backend/tests/test_maintenance.py`：按批次模型重写/更新（原 run_assists 同事务执行路径已移除）。

### 迁移 head

`0037_steward_assist_batches`（down_revision `0036_steward_production_scheduling`）。

### 已验证风险与边界

- 保存于 attempt 的 `output_json` 是解析并通过校验的产物（候选白名单字段/严格排列/截断后解释文本），不是原始 payload；prompt/response 明文仍只存 digest+长度。
- unknown 的处置：保守计费（预留不释放）+ 批次 failed + 不自动重发；确定性连接失败（connect_failed）与响应过大（response_too_large）记 failed，同样消耗预算。
- 预发送与写回两道栅栏共用 `_fence_check`（开关、job 终态、policy_version、provider revision/模型、facts 摘要+revision、卡片 state/revision）；evidence_hash 含源事实 revision，可检测改证据。
- 全局并发由"未过期 lease 批次数 < STEWARD_ASSIST_MAX_CONCURRENT_BATCHES（默认 1）"约束，非进程内信号量——跨进程靠 lease 行互斥。

### 未获得的证据（不得外推）

- 未对真实 LLM provider 做任何调用或 E2E；所有模型行为均由 fake transport 证明。
- 未验证多进程/多 listener 并发 lease 竞争下的实际表现（单进程测试 + BEGIN IMMEDIATE 语义推断）。
- 未跑前端/sidecar 套件（本任务未涉及）；`ruff check .` 全仓仍有非本任务文件的既有告警（spaces.py 导入排序等，属依赖任务未提交改动）。

## 检查记录（2026-09-11 check 轮）

- 逐 AC 复核：AC-1/AC-2/AC-3/AC-4/AC-5 的测试与代码证据均与"实施记录"一致（逐条读了测试体与 `steward_assist.py` 实现核对），无虚报。
- 附加验证：批次表仅被 models/steward_assist/steward(注释)/maintenance(注释) 引用，无 sidecar/通用 lease 路径可租；`steward_assist.py` 无 SourceFact 写入（只读经 steward 白名单 helper）；core `_execute_locked` 除 run_assists→register_batch_for_job 外未弱化授权检查；conftest 清表顺序 `steward_assist_batches` 先于 `steward_model_calls` 正确。
- 实际执行（exit 0 除非注明）：
  - `.venv/bin/python -m pytest -q`（backend 全量）→ 892 passed, 3 skipped
  - `.venv/bin/python -m pytest -q tests/test_steward_assist.py tests/test_maintenance.py`（修复后复跑）→ 49 passed
  - `.venv/bin/python -m mypy app` → 仅 2 个未触碰文件（space_model_settings.py / admin_agent.py，依赖任务未提交改动）共 5 条既有基线错误；本任务文件 0 错误。
  - `ruff check .` / `format --check .` → 本任务文件全部干净；剩余 7 条 ruff 告警 + 10 个 reformat 文件均在未触碰文件（spaces.py 导入排序、test_space_lineage_link、test_space_model_settings、0028 长行等，属依赖任务改动）。
  - 迁移往返：临时 DATA_DIR 上 upgrade head → downgrade 0036 → upgrade head，两次验证均 exit 0。
- 检查中发现并已修复（trivial）：
  - `migrations/versions/0037_steward_assist_batches.py`：upgrade() 内 ck_smc_status CHECK 替换块重复执行两次（原 99-101 行与 87-92 行等价冗余）；已删去冗余块，删除后迁移往返 + 目标测试复跑通过。
- 未修复/观察项（非本任务 BLOCKER）：
  - `recover_stuck_batches` 的孤儿批次兜底会给"历史上已 succeeded 但无批次"的 job 补登批次——开启辅助开关后升级部署时，老 job 可能在其原预算内触发新一轮模型调用（老 StewardModelCall 行计入该 job 预算可部分缓解）。属设计文档明示的兜底语义，但上线前运维应知晓。
  - attempt 在写回前不重验 input_hash（docstring 已声明；输出校验白名单兜底），风险由 `_fence_check` 的 evidence/卡片栅栏覆盖，可接受。
- 结论：PASS（无 BLOCKER/MAJOR；1 个 MINOR 已就地修复）。
