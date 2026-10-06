# `BEGIN IMMEDIATE` 逐处分类（2026-10-04）

## 真实规模

盘点口径修正：仓库不是「18 处」，而是 **43 个真实调用点**（另有 3 处只是 docstring 提及）：

- 25 处 `command_transaction(session, immediate=True)`
- 18 处 `_immediate_tx(db)`（`agent_queue.py` 8、`steward_assist.py` 7、`steward.py` 6 等）

两个 helper 的语义相同（驱动级 `BEGIN IMMEDIATE`，成功提交、异常整体回滚）：

```python
# app/commands/context.py:68 与 app/services/steward.py:170 与 app/services/agent_queue.py:75
sa_conn.exec_driver_sql("BEGIN IMMEDIATE")
```

`app/commands/context.py::_begin_immediate` 已按后端分派（非 SQLite 连接直接 return），
所以**代码不会报错**——这正是危险所在：锁消失后并发窗口静默打开。

## 分类结果

### 类别 A：单行/单条件状态转换 → 条件 UPDATE（CAS）

不需要显式锁。SQLite 上 `BEGIN IMMEDIATE` 在这里本来就不是必需的（见
`database-guidelines.md`「CAS 单独使用天然单赢家」）。转换判据：**被更新的行由唯一键确定，
且并发语义是「恰好一个赢」**。

| 位置 | 并发语义 | 目标实现 |
|---|---|---|
| `services/notifications.py::mark_notification_read` | 首个 `read_at` 胜出，幂等 | `UPDATE ... WHERE id=? AND read_at IS NULL` |
| `services/notifications.py::mark_all_notifications_read` | 批量标记 | `UPDATE ... WHERE recipient=? AND space=? AND read_at IS NULL` |
| `services/agent_queue.py::heartbeat` | 续租，仅租约持有者可续 | `UPDATE ... WHERE id=? AND lease_owner=?` |
| `services/agent_queue.py::request_cancel` | 取消标记幂等 | `UPDATE ... WHERE id=? AND cancel_requested=false` |
| `services/agent_queue.py::_settle` | 终态不可复活 | `UPDATE ... WHERE id=? AND status IN (leased,running)` |
| `commands/ownership.py::accept_transfer` | 双接受恰好一个 rowcount=1 | 已有条件 UPDATE，保留 |
| `services/invite_codes.py`（核销路径） | 核销恰好一个胜出 | 已有条件 UPDATE（`invite_codes.py:90` 已声明该设计） |

### 类别 B：选行 + 改状态（租约）→ 条件 UPDATE，**含配额时必须加 counter**

| 位置 | 关键点 | 目标实现 |
|---|---|---|
| `services/agent_queue.py::lease_next` | 选 queued job 改 leased | `FOR UPDATE SKIP LOCKED` 选行 + CAS 改状态 |
| `services/steward.py::lease_next_steward_job` | **含 `active_count` 计数**（每空间 active 上限） | counter 行 + `SKIP LOCKED` |
| `services/steward_assist.py::lease_attempt` | **含 `_in_flight_for_space` 计数**（per-space 上限） | counter 行 + `SKIP LOCKED`；**已实测证伪朴素移植** |

这三个是**唯一必须引入持久化 capacity counter 的位置**。其余租约类只需要候选行去重。
证据见 `lease-prototype.md` 与 `solution-decision.md`：朴素的
`SKIP LOCKED` + 计数子查询在 READ COMMITTED 下实测把「每租户上限 2」放成 5。

### 类别 C：多步 check-then-act → 锁父/协调行

被保护的是「读判定 → 写」的窗口，且存在**可锁的父资源**。

| 位置 | 读取→写入的窗口 | 目标实现 |
|---|---|---|
| `api/action_cards.py::execute_card` | 读卡状态/证据/成员资格 → 建家庭空间；**docstring 明说两个并发会各建一个空间** | 锁 `ActionCard` 行 |
| `commands/members.py::create_member` | 建档去重门禁 | 锁空间行 |
| `commands/members.py::create_managed_member` | 同上 + 绑定 | 锁空间行 |
| `commands/members.py::merge_duplicate_profile` | 双 profile 合并 | 锁两个 `User` 行（固定顺序） |
| `commands/spaces.py::create_shared_household` | 查已同属 household → 复用或新建 | 锁两个 user 行 |
| `commands/bindings.py::confirm_binding` | 读 binding+person → 改状态 | 锁 binding 行 |
| `services/steward_suggestions.py` ×3（dismiss/submit/restore） | 读 active 建议 → 改状态 | 锁空间行或建议行 |
| `services/steward_inferred.py` ×3（confirm/dismiss/reinstate） | 读 active 推断 → 改状态 | 锁空间行 |
| `services/steward.py::enqueue_steward_job` | 每空间至多一个活跃（幂等入队） | 锁空间行 + partial unique |
| `services/steward.py::settle_steward_job` | generation 校验 → 写终态 | 锁 job 行 |
| `services/steward.py::reaper_pass` | 选 stale → 写终态 | `SKIP LOCKED` + CAS |
| `services/steward.py::scan_due_spaces` | 读全部空间+schedule → upsert | `ON CONFLICT DO UPDATE` |
| `services/steward.py::flush_buffer` | 批量 apply_pair_result | 锁相关行 |
| `services/agent_queue.py::enqueue_run` | 每 session 一个 active + 每账户 N 个 | 锁 session 行 + counter |
| `services/agent_queue.py::submit_user_message` | 幂等键查重 → 插入 | 唯一约束 + `ON CONFLICT` |
| `services/agent_queue.py::reaper_pass` | 选 stale → 写终态 | `SKIP LOCKED` + CAS |
| `services/agent_queue.py::prune_finished` | 条件删除 | 单语句，无需锁 |
| `services/steward_pipeline.py::write_transaction` | 包住整个 pipeline 写事务 | 视内部操作而定 |
| `api/admin_steward.py::steward_delivery_retry` | 重试意图 + 审计 | 锁 intent 行 |
| `services/steward_assist.py::open_child_run` | 创建 run + 绑定 attempt | 唯一约束（`run_id` UNIQUE） |

### 类别 D：自然键可能尚不存在 → advisory lock + 唯一约束

没有可锁的父行（行可能还不存在）。

| 位置 | 自然键 | 目标实现 |
|---|---|---|
| `commands/registration.py::register_user` | 用户名唯一 | 唯一索引 + `ON CONFLICT` 捕获 |
| `services/admin_bootstrap.py::_bootstrap_admin_if_needed` | 「无 admin 时创建唯一 admin」 | `pg_advisory_xact_lock` + 唯一约束 |
| `commands/registration.py::create_my_invite_code` | 码唯一（已有 savepoint 重试） | 唯一约束兜底即可去锁 |
| `dev_seed.py::maybe_seed_demo_data` | 固定清单收敛比对 | advisory lock（开发脚本，低风险） |

## 必须同时修复的前置缺陷

**16 个局部唯一索引在 PostgreSQL 上会退化为全表唯一索引**（0 个声明了
`postgresql_where`）。已实测编译确认，其中 12 个会造成真实数据冲突
（如 `UNIQUE(session_id)` = 一个 session 一生只能有一个 run）。

详见 `partial-index-portability.md`。**这是 Phase B 的第一件事**：不先修，
任何历史数据导入都会在第一条历史行上因唯一冲突失败。

## 每处转换的验收要求

对类别 A/B/C/D 的每一处，都必须有一条**「去掉锁即失败」的并发回归**：

- 类别 A：两个并发调用，断言恰好一个成功且状态只前进一次。
- 类别 B：并发 worker 数 > 配额，断言实际并发数不超过配额（**这正是朴素移植失败的地方**）。
- 类别 C：并发执行「读判定 → 写」，断言不产生重复实体（如两个家庭空间）。
- 类别 D：并发创建同一自然键，断言恰好一行。

仅断言「最终状态正确」不足以守护——`database-guidelines.md` 已记录这个陷阱：
只含条件 UPDATE 的竞态在 pysqlite 上测不出锁缺失。并发用例必须带统一超时
（参照 `tests/test_person_dedupe.py` 的 `_SYNC_TIMEOUT` 模式），worker 只用标量 ID
与独立 `SessionLocal`。

## 尚未逐条核对的部分

类别 C 中 `services/steward.py::flush_buffer`、`services/steward_pipeline.py::write_transaction`
和 `services/steward_assist.py::open_child_run` 的内部写入集尚未逐行确认（它们通过
被调函数间接写入）。在转换这三处前必须先把被调链读全，不能按调用点表面分类。
