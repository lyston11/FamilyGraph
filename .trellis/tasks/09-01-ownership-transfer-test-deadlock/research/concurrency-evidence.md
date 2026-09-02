# 管理员交接并发测试研究记录

## 证据

- `backend/tests/test_ownership_transfer.py:221-260` 的 barrier/join 无 timeout，worker 捕获主线程 ORM 对象，并且只捕获 HTTPException。
- `backend/tests/test_person_dedupe.py` 展示了本项目的安全并发测试模式：标量 ID、有限 timeout、Lock 和普通异常可见性。
- `backend/app/commands/context.py` 已提供驱动级 `BEGIN IMMEDIATE`；数据库规范要求无法落唯一索引的检查-插入/检查-更新竞态使用立即事务。
- `backend/app/commands/ownership.py` 的 `accept_transfer` 当前在普通事务中进行 actor/transfer 读取和条件更新，存在 SQLite 读事务升级风险。
- 该测试当前偶尔单独通过，但既有全量记录在 deselect 它之后才稳定，说明通过不是修复证据。

## 决策

生产命令使用立即事务，测试使用独立 Session + 标量输入 + 有限同步。全量 pytest 恢复直接执行该用例；deselect 只能作为修复前诊断，不是交付方案。
