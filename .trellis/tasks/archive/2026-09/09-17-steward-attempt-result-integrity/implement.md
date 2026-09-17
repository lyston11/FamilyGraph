# B 实施记录（已执行）

状态：已实现并通过门禁。基线 `main@62a2b15`，实现提交 `e482f59`、`9c4a677`。

## 前置与隔离

- [x] 用户批准执行；`task.py start` 建立 worktree `../fg-09-17-steward-attempt-result-integrity`（分支 `feat/09-17-steward-attempt-result-integrity`）。
- [x] 未使用子智能体；与 A 未并行。
- [x] 分段读取 backend 指南、steward-action-card 完整叶文件（超 32768 字节注入上限，按记忆 #383 分段核对）。

## 1. 调用链与红测

- [x] 核对 `execute_batch` 全调用者、`Transport` 签名、`_apply_batch`、`recover_stuck_batches`、`terminology_target_retryable`、admin metrics 状态消费者。
- [x] S1 第一笔成功、第二笔慢：`test_returned_result_is_persisted_before_the_next_send`（修复前红）。
- [x] S2 成功 + failed 混合批次：`test_partial_batch_applies_independent_product_and_stays_failed`（修复前红）。
- [x] S3 合法空 items：`test_empty_terminology_result_is_marked_checked_and_not_resent`。
- [x] S4/S5 保存后崩溃、旧 owner 接管：既有 recovery 用例 + 改为在飞接管的 `test_old_executor_cannot_audit_after_another_session_takes_the_lease`。
- [x] S6 同 semantic_hash unknown 不重放：既有 `test_recovery_does_not_resend_audited_unknown_or_revisit_settled_failures` 继续通过。
- [x] S7 慢 chunk：`test_slow_chunks_cannot_extend_past_the_total_deadline`（修复前红，读完 10000 块约 540s）。
- [x] S8/S9/S10 输出过大、畸形输出、撤权/证据变化、四 kind 与 admin 指标：既有套件全部通过。

## 2. 最小实现

- [x] 抽出 `_settle_attempt`，复用原统一审计段逻辑；每笔在自身短事务内结算并提交后才发下一笔；事务内零网络。
- [x] 配套调整：`_apply_batch` 早退改为“先定终态码、再走栅栏、再应用独立产物”；`recover_stuck_batches` 的已有产物分支优先于 `has_unknown`。
- [x] 独立产物粒度沿用既有 kind 校验与写回栅栏；空结果沿 `_mark_terminology_checked`；未伪造 improvement。
- [x] `_post_json` 在读取循环内强制单调时钟总截止；policy-blocked 预留同事务释放为 `skipped`。
- [x] 零 schema 变更，无需迁移。

## 3. 验证

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_steward_assist.py tests/test_steward_terminology_runtime_quality.py \
  tests/test_steward_terminology_delivery_integration.py tests/test_steward_terminology_quality.py \
  tests/test_steward_terminology.py tests/test_admin_agent_latency.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
```

- [x] 定向套件全绿（96 passed）；后端全量 `1646 passed, 3 skipped`。
- [x] API/status 合同未变，未运行 frontend-api-smoke（无前端契约改动）。
- [x] 新增用例断言无泄露（仅状态/计数/哈希，无 prompt/响应原文）。
- [x] 手工自审 PRD 每条 AC（见 research/evidence/result-integrity-2026-09-17.md）。

## 4. 真实链路与完成

- [x] Spec 已更新（`steward-action-card.md` 逐笔结算 / 混合批次 / 总截止三条合同）。
- [ ] B-11 真实 Provider 小样本：未执行（本轮为程序合同与时限证据；需另行批准）。
- [x] 结果保全与总体速度分别汇报，未宣称上游提速。
- [x] commit → merge 到 main → push；archive 与 worktree/分支清理在收尾步骤执行。
