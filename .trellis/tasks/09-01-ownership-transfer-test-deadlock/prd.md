# 修复管理员交接并发测试死锁

## Goal

修复管理员交接双接受并发测试的间歇性永久阻塞和 SQLite 读事务升级风险，使并发测试具备有限等待、完整异常可见性和确定性单赢家语义，并恢复全量 pytest 对该测试的正常执行。

## Background / Confirmed Facts

- `backend/tests/test_ownership_transfer.py:221-260` 使用两个线程和 `threading.Barrier(2)`，但 barrier 和 join 都没有 timeout。
- 测试在主线程 `expire_all()` 后仍把 ORM 对象 `heir`、`transfer` 捕获到 worker 闭包中；SQLAlchemy Session/ORM 实例不能跨线程安全共享。
- 若任一 worker 在 barrier 前因懒加载、Session 或普通异常退出，另一个 worker 会永久等待；无 timeout 的 join 会把问题放大为全量 pytest 永久阻塞。
- `backend/tests/test_person_dedupe.py` 已有项目认可的并发测试模式：标量 ID、`_SYNC_TIMEOUT`、barrier/join timeout、普通异常捕获、Lock 保护结果列表和线程超时失败。
- `backend/app/commands/context.py` 已有 `BEGIN IMMEDIATE` 和 `command_transaction(immediate=True)`；SQLite 使用 WAL、busy_timeout=5000。
- `backend/app/commands/ownership.py` 的 `accept_transfer` 当前仍可能先读后写，再以普通事务执行条件 UPDATE；这保留了 SQLite 读事务升级为写事务的锁竞争窗口。
- 当前定向运行偶尔通过，不能证明历史间歇永久阻塞已修复；既有全量验证通过记录依赖 deselect 本用例。

## Requirements

### OTD-F1：修复并发测试同步结构

- worker 只接收 `transfer_id`、`heir_id`、`heir_account_id`、`space_id` 等普通整数，不捕获主线程 ORM 实例或 Session。
- 每个 worker 创建并关闭自己的 `SessionLocal`，在自己的 Session 中加载必要数据。
- barrier.wait 和 thread.join 使用统一 `_SYNC_TIMEOUT`；超时必须快速失败并释放测试资源。
- worker 捕获 HTTPException 以及普通 Exception，记录异常类型/信息，不能静默退出。
- 结果列表由 Lock 保护；最终结果和数据库状态从主线程重新查询。

### OTD-F2：修复交接命令事务边界

- `accept_transfer` 使用 `command_transaction(session, immediate=True)`。
- actor 加载、transfer 查询、过期/状态检查、条件状态更新、角色翻转和审计/事件写入位于同一立即事务边界内。
- 保留条件 UPDATE 的数据库单赢家语义：两个接受者最多一个 `won`，另一个得到既有业务 409。
- 不引入进程级 Python lock，不把 SQLite 锁异常伪装成业务冲突。

### OTD-F3：恢复完整验证

- 并发测试连续运行至少 20 次，无永久阻塞。
- 每次恰好一个 worker 返回 `won`，另一个返回 `409`。
- 最终只有一条 accepted transfer、一个 active `space_admin`，owner 只翻转一次。
- 移除全量 pytest 对该用例的永久 deselect，并让全量测试直接执行该用例。

## Acceptance Criteria

- [ ] 测试不再跨线程共享 ORM 实例/Session。
- [ ] Barrier、join 和 worker 异常都有有限等待与可见失败。
- [ ] `accept_transfer` 使用立即事务，授权检查和条件写入没有读事务升级窗口。
- [ ] 并发测试连续 20 次通过且无挂死，结果稳定为 `won` 与 `409`。
- [ ] 最终数据库只有一个 accepted transfer 和一个 active `space_admin`。
- [ ] 全量 pytest 不再需要 deselect 该测试。
- [ ] 后端 Ruff、format、mypy 和相关 pytest 通过。

## Constraints

- 必须遵守 `.trellis/spec/backend/database-guidelines.md` 中“无法落唯一索引时使用 BEGIN IMMEDIATE”的约束。
- 不修改 ownership transfer 的产品 FSM、资格规则或错误码语义。
- 不覆盖当前工作树中与本任务无关的修改；尤其保留 guest 角色收窄任务的 WIP。

## Out of Scope

改变所有权转移业务规则、增加外部重试队列、重写 SQLite 数据层、修改其他并发命令、以 deselect 作为最终解决方案。
