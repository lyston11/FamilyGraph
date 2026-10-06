# Gate 2：静态调用图与锁序分析

方法：DFS-first-reach (not line-number order; cross-file line numbers are incomparable)

冻结顺序：`global_write_lock → counter → run_row`

- 事务入口：**63**
- 可达两类以上锁类型：**44**
- 违反冻结顺序：**0**
- counter 已实现：**False**

## 可达多个锁类型的入口

| 入口 | 锁序列（DFS 序） | 违反顺序 |
|---|---|---|
| `api/action_cards.py::execute_card` | run_row | 否 |
| `api/admin_steward.py::steward_delivery_retry` | run_row | 否 |
| `commands/ownership.py::accept_transfer` | run_row | 否 |
| `commands/registration.py::create_my_invite_code` | run_row | 否 |
| `commands/registration.py::redeem_invite_code` | run_row | 否 |
| `commands/registration.py::register_user` | run_row | 否 |
| `services/agent_queue.py::_settle` | run_row | 否 |
| `services/agent_queue.py::enqueue_run` | run_row | 否 |
| `services/agent_queue.py::heartbeat` | run_row | 否 |
| `services/agent_queue.py::prune_finished` | run_row | 否 |
| `services/agent_queue.py::request_cancel` | run_row | 否 |
| `services/agent_queue.py::submit_user_message` | run_row | 否 |
| `services/notifications.py::mark_all_notifications_read` | run_row | 否 |
| `services/steward.py::_rebuild_space_derived` | run_row | 否 |
| `services/steward.py::settle_steward_job` | run_row | 否 |
| `services/steward_assist.py::apply_settled_attempt` | run_row | 否 |
| `services/steward_assist.py::heartbeat_child_run` | run_row | 否 |
| `services/steward_assist.py::lease_attempt` | run_row | 否 |
| `services/steward_assist.py::recover_stuck_attempts` | run_row | 否 |
| `services/steward_assist.py::settle_attempt` | run_row | 否 |
| `services/steward_delivery.py::_claim_due` | run_row | 否 |
| `services/steward_delivery.py::_defer_changed_snapshot` | run_row | 否 |
| `services/steward_delivery.py::_record_failure` | run_row | 否 |
| `services/steward_delivery.py::drain` | run_row | 否 |
| `services/steward_demand.py::register` | run_row | 否 |
| `services/steward_gc.py::collect` | run_row | 否 |
| `services/steward_inferred.py::confirm_edge` | run_row | 否 |
| `services/steward_inferred.py::dismiss_edge` | run_row | 否 |
| `services/steward_inferred.py::reinstate_edge` | run_row | 否 |
| `services/steward_overlay.py::_heartbeat` | run_row | 否 |
| `services/steward_overlay.py::claim_due` | run_row | 否 |
| `services/steward_overlay.py::execute` | run_row | 否 |
| `services/steward_pipeline.py::_fail_target` | run_row | 否 |
| `services/steward_pipeline.py::_reserve_search` | run_row | 否 |
| `services/steward_pipeline.py::_reuse_view` | run_row | 否 |
| `services/steward_pipeline.py::_stage_view` | run_row | 否 |
| `services/steward_pipeline.py::execute` | run_row | 否 |
| `services/steward_pipeline.py::heartbeat` | run_row | 否 |
| `services/steward_pipeline.py::publish` | run_row | 否 |
| `services/steward_pipeline.py::record_failure` | run_row | 否 |
| `services/steward_pipeline.py::save_target` | run_row | 否 |
| `services/steward_suggestions.py::dismiss_suggestion` | run_row | 否 |
| `services/steward_suggestions.py::restore_term` | run_row | 否 |
| `services/steward_suggestions.py::submit_suggestion` | run_row | 否 |

## 结论与限定

`violations = 0` **不是**「顺序正确」的证明：
代码中尚无 counter 锁，冲突的一方不存在。本图是**回归基线**——实现 counter 后重跑，
`entries_violating_frozen_order` 必须仍为 0。

## 关键发现：租约入口已在取 run 行

三个配额入口的当前行锁序列：

| 入口 | 行锁 |
|---|---|
| `services/agent_queue.py::lease_next` | （无） |
| `services/steward.py::lease_next_steward_job` | （无） |
| `services/steward_assist.py::lease_attempt` | run_row |

`lease_attempt` **已经**取 `run_row`（经 fence 或 `acquire_run_writer`）。因此引入 counter 时，
它必须排在 `run_row` **之前**，否则租约路径本身就是 `run_row → counter`，
与结算路径同向、但与冻结顺序相反。

结算入口（同样先取 run 行）：

| 入口 | 行锁 |
|---|---|
| `services/agent_queue.py::_settle` | run_row |
| `services/steward.py::settle_steward_job` | run_row |
| `services/steward_assist.py::settle_attempt` | run_row |

**这就是 counter 归还必须早于 fence 的原因**：`_settle` 与 `settle_attempt` 都在
函数体开头取 run 行，若 counter 归还写在其中（或其后），实际锁序即 `run_row → counter`。
已实测该反向在 PostgreSQL 上抛 `DeadlockDetected`（`lock-order-deadlock.md`）。

## 方法学修正记录（两次都踩坑，记录以免重犯）

1. 第一版按行号排序推断顺序，**跨文件比较行号无意义**，误报 32 个「违反」。
2. 第二版改为 DFS 序，但仍把 `global_write_lock` 与行锁一起排序——而它是
   **事务信封**（`BEGIN IMMEDIATE` 在函数体之前取得），包裹整个事务，
   不可与行锁比较，否则「进入事务」被误判为「反向取锁」（误报 5 个）。
3. 本版把信封与行锁分离，violations 归零，且结论可解释。
