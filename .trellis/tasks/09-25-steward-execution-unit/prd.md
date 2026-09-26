# E1 Steward 执行单元重构：attempt 级 lease、per-space 并发、单链路

> 父任务：`09-25-steward-pi-child-run-design`。技术权威是父任务 `design.md`（重写版）。
> 本任务**只做架构性改动**（E1），不做逐 kind 载体切换（E2..E5）。

## Goal

把 Steward 模型辅助的执行单元从「批次」改成「一次模型调用（attempt）」，使
in-process 与 Pi child run 走**同一条链路**，并发作用域从全库 1 改成按空间。

## Requirements

### R1 执行单元 = attempt

- `steward_model_calls` 持有 `lease_owner` / `lease_until` / `next_attempt_at` / `carrier`。
- `steward_assist_batches` 收窄为不可变工作快照 `steward_assist_plans`，移除全部执行状态
  （`status` / `attempt` / `next_attempt_at` / `lease_owner` / `lease_until` / `error_code` /
  `updated_at`）。
- plan 无状态；「这批工作结果如何」由 attempt 派生（`plan_outcome` / `plan_error_code`），
  派生规则必须逐条复现旧 `_apply_batch` 的终态码规则（其他层读这些码）。

### R2 单一链路

```
plan_for_job → lease_attempt → carrier.execute → settle_attempt → recover_stuck_attempts
```

- 旧函数 `execute_batch` / `schedule_due_batch` / `launch_batch` / `_execute_in_own_session` /
  `_reserve_due_attempts` / `_apply_batch` / `_finish_batch_after_attempts` 全部删除。
- 载体抽象 `AssistCarrier`（`inproc` / `pi`），`carrier` 是 attempt 的一个字段。
- HTTP 永不发生在业务写事务内：发送所需的一切（runtime、投影、输出上界）在租约事务内读出，
  出事务后才发送。

### R3 fence 调用点收敛

- `_fence_check` 只允许三个调用点：`lease_attempt`（发送门）、`settle_attempt`（写回门）、
  `recover_stuck_attempts`（补做被中断的写回）。
- **发送门必须 sweep 而不是单次取行**：栅栏不过的 attempt 当场落 `skipped` + 安全原因码，
  循环继续看下一个候选。否则一个被栅栏拦下的 attempt 会让本空间永远停在 `reserved`
  （没有别的地方会再租它），且 `plan_error_code` 永远为空。

### R4 并发按空间

- `STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE`（默认 2，上界 8）取代
  `STEWARD_ASSIST_MAX_CONCURRENT_BATCHES`（全库 1）。
- 两个空间各有到期 attempt 时必须**同时**可租。

### R5 保留既有合同（不得削弱）

- attempt 状态机不变：`reserved → in_flight → succeeded|failed|degraded|unknown|skipped`。
- `unknown` 保守计费且**不自动重发**（memory #407）。
- 封闭输出 schema 校验、写回栅栏、CAS 应用、attempt 级预算、四个崩溃恢复点语义不变。
- 行为等价：四种 kind 全部走 `inproc`，`steward_model_calls.run_id` 全 NULL。

## Acceptance Criteria

- [x] AC1 迁移 `0055_steward_assist_execution_unit` 重写；refusal guards 先于任何 DDL
      （memory #398）；downgrade 是忠实逆（结构性断言列/FK/索引/CHECK 形状），
      且不把已修正数据破坏性恢复（memory #399）。
- [x] AC2 `upgrade → downgrade → upgrade` 在隔离 `DATA_DIR` 往返通过。
- [x] AC3 行为等价：`run_id` 全 NULL，现有 steward 测试语义不变（`transport=` 注入点保留）。
- [x] AC4 per-space 并发可证：两空间同时可租；同空间第二个租约被预算挡下（且挡下的是预算
      而不是「无工作」——用变异测试确认）。
- [x] AC5 fence 调用点集合被结构性断言（AST 检查，恰好三个具名调用者）。
- [x] AC6 发送门 sweep 有回归：被栅栏拦下的 attempt 不阻塞同空间的兄弟 attempt
      （变异测试确认删掉 sweep 后失败）。
- [x] AC7 全量检查：backend pytest 全绿 + ruff check/format + mypy。

## Notes

- **本轮修掉的两个真实缺陷**（在 AC5/AC6 的回归落地前存在）：
  1. 发送门从 `schedule_due_attempt` 移出后**没有**在 `lease_attempt` 补上 sweep，
     导致被栅栏拦下的 attempt 永不退休；三个「provider 不可用 / 云同意撤销 / 要求本地」
     的降级用例因此失败（零发送合同本身没坏，坏的是「谁负责退休」）。
  2. 该 sweep 与 `schedule_due_attempt` 并存时会出现**两个**发送门，两侧可能对
     「哪个 attempt 被退休」判断不一致。
- 已知残留（非本任务范围）：`tests/test_invitation_reachability.py` 在 main 上就有
  2 条 ruff 违规（E501 / F841），与 E1 无关，未触碰。
- E1 不启用 Pi 载体：`PiCarrier.execute` 抛 `NotImplementedError`，E3 实现。
