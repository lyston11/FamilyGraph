# B 结果保全：验收证据（2026-09-17）

基线 `main@62a2b15`；实现提交 `e482f59`（fix）、`9c4a677`（empty 检查回归）。
全部为隔离 DATA_DIR + 合成 transport 的可重复回归，未写生产库、未发真实模型请求。

## 1. 已确认缺陷（修复前）

| # | 位置 | 缺陷 |
|---|---|---|
| D1 | `steward_assist.py` `execute_batch` | 结果只累积在内存 `results`，循环结束后才统一审计；后续慢请求耗尽租约时，早先已返回结果落不了库 |
| D2 | `steward_assist.py` `_apply_batch` | 在业务栅栏之前因同批 `unknown`/`failed` 直接 `failed` 返回，不应用独立成功产物 |
| D3 | `steward_assist.py` `recover_stuck_batches` | `has_unknown` 分支优先于 `applying`+已有产物，已持久化成果无法进入恢复应用 |
| D4 | `steward_assist.py` `_post_json` | `httpx.Client(timeout=...)` 是阶段/IO 等待限制，慢分块可持续占用执行线程 |

## 2. 红→绿证据

| AC | 回归用例 | 修复前 | 修复后 |
|---|---|---|---|
| B-01 | `test_returned_result_is_persisted_before_the_next_send` | 红：第二笔发送时第一笔仍非终态 | 绿：独立 Session 在第二笔在飞期间读到 `["succeeded","in_flight"]` 且已有 `output_json` |
| B-02 | 同上 + `test_partial_batch_applies_independent_product_and_stays_failed` | 红：`reason_text_llm` 计数 0 | 绿：独立产物已应用（计数 1），批次仍 `failed`/`transport_failed` |
| B-03 | `test_empty_terminology_result_is_marked_checked_and_not_resent` | — | 绿：`output_json={"items":[]}`、`last_checked_hash` 已写、同语义不再入组、`term is None`（不伪造改善） |
| B-04 | `test_recovery_does_not_resend_audited_unknown_or_revisit_settled_failures` | 绿 | 绿：恢复取得新 owner/attempt，重复恢复返回 0，无新 HTTP |
| B-05 | 同上 | 绿 | 绿：`unknown` 保持 unknown、保守计费、不重发 |
| B-07 | `test_slow_chunks_cannot_extend_past_the_total_deadline` | 红：读完全部 10000 块（约 540s） | 绿：0.3s 总截止处中止（实测 0.3–3.0s 内），连接由上下文管理器关闭 |
| B-09 | `test_old_executor_cannot_audit_after_another_session_takes_the_lease`（改为在飞接管） | — | 绿：旧执行者零结算（无 `output_json`/`billed_tokens`），接管后下一笔未发送 |

红态验证方式：`git stash push backend/app/services/steward_assist.py` 后重跑，两处新用例与慢分块用例均失败；`git stash pop` 后恢复全绿。

## 3. 设计选择的实测依据

- **失租旧执行者仍拒写**：逐笔结算前重验 `(lease_owner, attempt)` 与 `lease_until`；不符则直接返回，不结算。原“旧执行者不能补审计”测试保留并改为在飞接管场景，断言更严（含 `billed_tokens is None`）。
- **混合批次终态**：`succeeded`+`unknown` → 批次 `failed` + `error_code=network_unknown`，但独立产物照常应用；`succeeded`+`failed` → `failed`+`transport_failed`。共同栅栏失效仍整批 `superseded`（`test_midflight_input_change_discards_model_writeback` 三条参数化用例继续通过）。
- **零 schema 变更**：复用 `StewardModelCall` 既有 `status/latency_ms/usage/billed_tokens/output_json` 与 `StewardAssistBatch` 既有 owner/attempt/deadline，未新增迁移。
- **总截止**：不引入异步化；在既有 `iter_bytes` 读取循环内按 `time.monotonic()` 强制总截止，保留原有阶段 timeout 作为分项上界。未发送的 policy-blocked 预留改为同事务释放（`skipped`），不标 unknown、不计上游费用。

## 4. 边界与未做

- 未放开 unknown 重放；未给历史 unknown 回填；未调大超时/租约；未换 Provider/模型；未提高并发。
- 收到响应后、提交前进程退出仍是 `unknown`（如实保留，未宣称“任何结果都必保全”）。
- 未做真实 Provider 小样本（B-11）：本轮为程序合同与时限证据，真实改善仍受父任务 AC 与上游速度约束。
- 速度结论：本次只证明减少系统自身丢失，**不代表**上游更快；端到端速度由父任务基线判定。

## 5. 门禁

```text
backend: ruff check ✓ / ruff format --check ✓ / mypy app ✓
         pytest 1646 passed, 3 skipped（含全部既有 steward/terminology/admin 回归）
```
