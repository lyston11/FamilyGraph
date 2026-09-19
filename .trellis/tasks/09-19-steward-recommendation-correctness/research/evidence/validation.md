# 回归证据：红测、覆盖与守护力验证

任务：`09-19-steward-recommendation-correctness`
基线：`c555a4d`（main），worktree `feat/09-19-steward-recommendation-correctness`
环境：本机隔离 `DATA_DIR`（pytest `conftest.py` 自动 `tempfile.mkdtemp`），未触碰生产库。

## 1. 红测：既有用例编码了错误前提

改动前 `tests/test_action_cards_api.py` 的 5 个用例在 `spouse_scene` 中把**夫妻双方放进同一个 household 空间**，然后断言可以执行 `create_household`。这正是用户报告的误报场景（已共同在一个家庭里仍推荐共建），说明测试夹具本身把 bug 当作期望行为。

| 用例 | 改动前行为 | 改动后 |
| --- | --- | --- |
| `test_list_cards_happy_and_guards` | 共享 household 中出卡并列出 | 夹具改 lineage；新增共享 household 时列表为空 |
| `test_view_dismiss_accept_happy_and_state_branches` | 同上 | 同上 |
| `test_terminal_card_returns_410` | 同上 | 同上 |
| `test_concurrent_accept_one_wins_one_409` | 同上 | 同上 |
| `test_execute_create_household_success_and_no_lineage_merge` | 共享 household 中执行成功 | 夹具改 lineage；执行成功语义保持 |

红测证据（改动前运行，`reason=household_already_shared`）：

```text
FAILED tests/test_action_cards_api.py::test_execute_create_household_success_and_no_lineage_merge
E  AssertionError: {"error":{"code":"CARD_EXECUTE_REJECTED",...
   "detail":{"card_id":1,"reason":"household_already_shared"}}}
E  assert 409 == 200
```

5 failed, 64 passed（`test_steward.py` + `test_action_cards_api.py` + `test_steward_inferred.py`）

同类红测：`tests/test_steward_suggestions.py::test_owner_submit_creates_proposal_endpoint_confirms`
第二个建议用 `direct_sibling`，而该对已有 `adoptive_parent`；新判据按设计拒绝 → 该用例改用 `partner` 以保留其原意（非端点不能代确认）。

## 2. 守护力验证：并发用例确实守护写锁

`test_two_workers_executing_household_cards_create_exactly_one_space` 断言两个执行者并发 execute 只创建一个空间。

把 `app/api/action_cards.py::execute_card` 的 `command_transaction(db, immediate=True)` 改回 `command_transaction(db)`（去掉 `BEGIN IMMEDIATE`），**连续 5 次稳定失败**：

```text
E  AssertionError: [200, 200]
E  assert 200 in (409, 410)
```

5/5 failed。恢复写锁后 20 passed。结论：该用例真正守护「读资格 → 写空间」的原子性，不是恒真断言。

## 3. 新增回归覆盖

| 文件 | 新增用例 | 覆盖 |
| --- | --- | --- |
| `tests/test_steward_candidate_policy.py`（新） | 15 | 直接亲子双向、收养/继亲、生物祖先长链双向、多代、混合继养链不归约、guardian/spouse/partner 不排斥、无冲突对照、环与自环、畸形行跳过、预算 fail-closed、空索引 |
| `tests/test_steward_inferred.py` | 4 | 冲突候选不投影（同批合法候选保留）、祖孙冲突不投影、存量冲突边按未变 hash 退役且展示隐藏、冲突边 confirm 被拒且不新增 SourceFact |
| `tests/test_steward_suggestion_quality.py` | 4 | 冲突候选不产生建议/recipient、存量建议有效状态为 superseded 且无 submit、提交被拒无副作用、无冲突线索仍可提交 |
| `tests/test_steward.py` | 7 | lineage 中共享 household 抑制、无共同 household 正常出卡、lineage_request 保留、pending/rejected/退出不计数、profile ref 与共同 lineage 不计数、存量卡按未变 hash 退役、accepted 卡经 staged card_review 退役 |
| `tests/test_action_cards_api.py` | 6 | 列表隐藏、accept 拒绝、读后共享再 execute 拒绝、两张旧卡先后执行只建一个家庭、lineage 卡保留、两 worker 并发只建一个空间 |
| `tests/test_steward_overlay_conflict_regression.py`（新） | 1 | 修复前已缓存的 overlay 行含冲突边时不再被服务（已破环验证：去掉过滤即失败） |
| `tests/test_steward_staged_pipeline.py` | 1 | overlay 缓存中冲突边不再显示，且缓存行不被读路径改写 |

自审阶段另修复一处真实缺陷（`source_state` 覆盖 `resolved`）并补回归
`test_confirmed_sibling_stays_resolved_despite_unrelated_conflicting_parent`；
详见 `evidence/independent-check.md`。

## 4. 存量 dry-run 工具

新增只读脚本 `backend/scripts/steward_recommendation_dryrun.py`（仅 SELECT，不提供批量清理路径）。

隔离库合成样本实测：

```text
DATA_DIR: /tmp/fg-dryrun4-A7ls
== candidates: total=1 hits=1
   id=1 space=1 status=proposed reason=confirmed_parent_child
== suggestions: total=2 hits=2
   id=1 space=1 status=proposed  reason=confirmed_parent_child
   id=2 space=1 status=submitted reason=confirmed_parent_child [人工核查，不自动处理]
== inferred_edges: total=1 hits=1
   id=1 space=1 status=proposed reason=confirmed_parent_child
== action_cards: total=1 hits=1
   id=1 space=1 status=pending reason=household_already_shared
== protected: {'confirmed_source_facts': 1, 'spaces': 2}
```

要点：总量与命中量分列；已关联事实的 `submitted` 建议标记为人工核查而非自动处理；脚本未在隔离目录之外创建任何库文件。

## 5. 未验证与限制

- **未读取生产库**。本任务不声称线上对象的实际数量或状态；正式收敛必须在 S7 授权后用同一 dry-run 逐条重验，并禁止按「26 条 / 21 张」等旧数字筛选。
- 未做真实模型质量评测；本任务的安全屏障是确定性代码，不依赖模型行为。
- 未部署；代码通过不等于线上生效。
