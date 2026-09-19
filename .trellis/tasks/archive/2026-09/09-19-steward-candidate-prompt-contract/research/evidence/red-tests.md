# 红测证据：候选 prompt 契约与规模降级

基线 SHA：`4d6fb284a378c07e84b3954cc62e0fb5a6a9f0ec`（main，任务 worktree 起点）
worktree：`/Users/lyston/PycharmProjects/fg-09-19-steward-candidate-prompt-contract`

## 命令

```bash
cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_candidate_prompt_contract.py
```

结果：`9 failed in 7.27s`。

## 逐条失败原因（均来自目标缺陷，非夹具/环境）

| 用例 | 失败断言 | 归因 |
| --- | --- | --- |
| `test_candidate_prompt_declares_direction_contract` | `AttributeError: _CANDIDATE_DIRECTION_CLAUSE` | prompt 无方向语义条款（R1） |
| `test_candidate_prompt_declares_conflict_and_duplicate_ban` | `AttributeError: _CANDIDATE_CONFLICT_CLAUSE` | prompt 无矛盾/重复禁止条款（R2） |
| `test_candidate_prompt_has_parseable_minimal_example` | `AttributeError: _CANDIDATE_EXAMPLE_CLAUSE` | prompt 无最小示例（R3） |
| `test_prompt_version_tracks_prompt_change_and_is_stable` | `AttributeError: prompt_version` | 版本只存在于测试侧 `_prompt_version()`（R4） |
| `test_prompt_too_large_settles_batch_as_failed_with_reason` | `assert 'applied' == 'failed'` | 超限静默降级（R5/AC4） |
| `test_prompt_too_large_counted_in_admin_metrics` | `KeyError: assist_skipped` | `assist_counts` 无 `skipped`（R6/AC5） |
| `test_oversized_fact_set_is_bounded_and_deterministic` | `AttributeError: candidate_user_content` | 无有界子集（R7/AC6） |
| `test_untruncated_projection_is_byte_identical` | `AttributeError: candidate_user_content` | 同上（AC7 对照） |
| `test_oversized_space_still_validates_candidate_output` | `assert calls` → `[]` | 超限时根本不发送（R7/AC6） |

## 对照（未失败的既有行为，确认夹具有效）

- 夹具能真实造出超限事实集：`len(full) + system_bytes > cap` 断言在红测中通过。
- `test_prompt_too_large_settles_batch_as_failed_with_reason` 中 `calls == []`、
  `rows` 全为 `skipped` + `prompt_too_large`、`billed_tokens is None` 均通过——
  证明失败点确实只在「批次终态」这一项，不是发送路径。

## 未验证

未读取生产库；不声称线上已发生该降级（规划边界保持）。
