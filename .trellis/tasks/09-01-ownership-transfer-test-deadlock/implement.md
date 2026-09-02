# 管理员交接并发测试死锁实施计划

## 1. 实施前检查

- [ ] 读取本任务 PRD、design、研究文件以及架构/数据库规范。
- [ ] 检查当前工作树和现有 pytest 配置，确认 deselect 来源及 unrelated WIP。
- [ ] 对照 `test_person_dedupe.py` 的 `_SYNC_TIMEOUT` 并发模式，确定复用方式而不是复制不一致常量。
- [ ] 确认 `command_transaction(immediate=True)` 的调用约定和 ownership command 当前异常路径。

## 2. 改造并发测试

- [ ] 在 `test_concurrent_double_accept_single_winner` 中提取 transfer/heir/space 的标量 ID。
- [ ] 每个 worker 使用独立 SessionLocal，并在本 Session 中重新加载 actor 所需数据。
- [ ] 为 barrier.wait 和 join 添加统一超时。
- [ ] 捕获普通 Exception，使用 Lock 保护结果列表，并在 worker finally 关闭 Session。
- [ ] 线程未在 timeout 内结束时显式失败，避免测试本身永久挂起。
- [ ] 最终重新查询 transfer、space 和 active space_admin，删除对跨线程 ORM 对象的断言。

## 3. 改造 ownership command

- [ ] 将 `load_actor` 移入 `command_transaction(session, immediate=True)`。
- [ ] 保持 transfer 条件 UPDATE、`rowcount` 单赢家和现有 409 业务语义。
- [ ] 确认所有角色/owner/event/audit 写入仍在同一事务中。
- [ ] 增加锁竞争/重复接受回归断言；不加入 Python 全局锁或错误码转换。

## 4. 移除临时测试绕过

- [ ] 删除 pytest 配置、脚本或文档中对该并发用例的永久 deselect。
- [ ] 搜索仓库确认没有新的永久 deselect；历史记录可以保留为说明，但不能作为测试命令默认参数。

## 5. 验证命令

定向并发重复：

```bash
cd backend && for i in $(seq 1 20); do .venv/bin/python -m pytest -q tests/test_ownership_transfer.py::test_concurrent_double_accept_single_winner || exit 1; done
```

相关测试与质量门禁：

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_ownership_transfer.py
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app
cd backend && .venv/bin/python -m pytest -q
```

全量命令必须直接包含并发用例；若环境需要临时缩小范围，只能在命令行临时说明，不能提交永久 deselect。

## 6. 停止点与交付检查

- [ ] 任一 worker 仍可能永久等待：停止，不继续跑全量，先修同步结构。
- [ ] 出现 SQLite lock 错误：保留完整 traceback，检查事务边界，不把它改写为 409。
- [ ] 最终出现零/双管理员：停止，回到 `_apply_transfer_ownership` 事务不变量。
- [ ] PRD/design/implement 与实际改动一致，manifest 已有真实研究条目。
- [ ] 仅在用户明确批准最终规划摘要后执行 `task.py start`；当前阶段保持 planning。
