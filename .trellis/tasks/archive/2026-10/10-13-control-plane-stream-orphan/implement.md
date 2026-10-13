# 实施记录

- [x] `capacity_bootstrap`：新增 `_UNRECONCILED_RESOURCES = {provider_stream}`，
      `reconcile` 的孤儿循环跳过该维度
- [x] 新增两条回归：`test_in_flight_stream_is_not_an_orphan`、
      `test_ready_passes_with_an_in_flight_stream`
- [x] 反证：移除例外后两条新测试失败；还原后通过
- [x] 既有孤儿检测（其他维度）保持通过

## 验证

- 全量 `pytest`：2495 passed / 0 failed
- 受影响面 79 passed（capacity_bootstrap / stream_capacity /
  cluster_capacity_admission / capacity_counters / capacity_release_gate / provider_proxy）
- `mypy` Success；`ruff check` / `format --check` 通过
