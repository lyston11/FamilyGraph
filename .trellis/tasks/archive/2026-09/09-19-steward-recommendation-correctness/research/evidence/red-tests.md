# 红测记录

基线 SHA：`c555a4d`（main）。执行目录：任务 worktree `backend/`。

## 红测 1：夹具编码了错误前提（共享 household 仍推荐共建）

命令：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_steward.py tests/test_action_cards_api.py tests/test_steward_inferred.py
```

结果：`5 failed, 64 passed`

失败断言（代表）：

```text
tests/test_action_cards_api.py::test_execute_create_household_success_and_no_lineage_merge
E  AssertionError: {"error":{"code":"CARD_EXECUTE_REJECTED","message":"当前条件已发生变化，暂时无法执行",
   "detail":{"card_id":1,"reason":"household_already_shared"}}}
E  assert 409 == 200
```

原因确认：夹具 `spouse_scene` 把夫妻双方都放进**同一个 household 空间**，然后断言可执行共建——正是目标 bug 场景。失败来自目标行为，不是环境或造数错误。

## 红测 2：提交路径缺少语义重验

```text
tests/test_steward_suggestions.py::test_owner_submit_creates_proposal_endpoint_confirms
E  HTTPException: 409: {'__api_error__': {'code': 'SUGGESTION_STATE_CONFLICT', 'message': '建议已失效'}}
```

原因确认：该用例第二个建议用 `direct_sibling`，而同一对已有 `adoptive_parent`；新负向判据按设计拒绝。这是**预期**的行为变化，用例已改用 `partner` 保留其原意（验证非端点不能代确认）。

## 红测 3：并发用例守护力

把 `execute_card` 的 `BEGIN IMMEDIATE` 改回普通事务后：

```text
tests/test_action_cards_api.py::test_two_workers_executing_household_cards_create_exactly_one_space
E  AssertionError: [200, 200]
E  assert 200 in (409, 410)
```

连续 5 次运行：5/5 failed。恢复写锁后通过。证明该用例真正守护写锁，而非恒真。
