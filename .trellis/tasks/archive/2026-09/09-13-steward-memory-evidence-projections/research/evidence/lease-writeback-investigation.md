# 写锁等待后的 assist 租约检查

## 结论

MR-23 主线程审查发现 `_apply_batch` 只将 `lease_until` 与调用方预采样的 `now` 比较。`steward._immediate_tx` 先执行真实 `BEGIN IMMEDIATE`，其取锁等待可以跨过租约截止时间；即使 owner/attempt 未变，进入写事务的执行者也可能已经过期。

原证据位置：`backend/app/services/steward_assist.py::_apply_batch` 的 lease 检查与 `backend/app/services/steward.py::_immediate_tx` 的 BEGIN；对照 `steward_delivery._matches_claim` 已在结果事务内使用现场时间。

## 失败复现

`test_steward_candidate_evidence_integration.py::test_lease_expiring_while_actual_writeback_waits_for_sqlite_writer_is_not_adopted`：

1. 真实 core/delivery 注册批次，fake transport 通过真实输出校验，保留已成功的模型结果，停在 apply 前模拟恢复点。
2. 保存调用方采样时间和 250ms 后到期的 batch lease。
3. 独立 Session 持有真实 `BEGIN IMMEDIATE`，另一个线程执行真实 `_apply_batch`；包装事务入口仅用于观测，不替代取锁。
4. 持锁超过 deadline 后释放，要求旧执行者仍返回 `applying`，且没有 candidate 或 evidence version。

修复前实施者的命令与结果（2026-09-15）：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_candidate_evidence_integration.py -k 'existing_private or real_jobs_version or lease_expiring' --tb=short
```

`2 passed, 1 failed`，4.08 秒。两条真实多作业主链通过；失败为锁等待反例：实际返回 `applied`，期望 `applying`。该结果证明过期的应用会被旧时间放行，不是伪造候选或 mock 数据库锁造成的夹具错误。

## 采用的修复

在取得 writer 后重新采样 UTC，使用 `max(caller_now, live_now)` 做批次租约与本次记录时间校验。调用方为恢复测试传入未来时间时仍可收紧检查；旧调用时间不能延长真实租约。保持 owner/attempt、Provider/输入、模型结果保留和恢复语义，无新模型调用或配置项。

这是 SP-AC3 的实现缺口修复，仍在本任务 assist 写回范围内。最终绿测和完整后端结果见 [验证记录](../validation.md)；本文件保留失败原因与反例，不把初轮失败记作通过。
