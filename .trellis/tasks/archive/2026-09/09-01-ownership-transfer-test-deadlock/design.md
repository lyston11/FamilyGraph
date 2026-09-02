# 管理员交接并发测试死锁技术设计

## 1. 根因与边界

该问题由两个独立缺陷叠加：测试同步没有超时且跨线程捕获过期 ORM 对象；生产命令先执行读取/授权，再尝试把 SQLite 读事务升级为写事务。测试需要确保异常可见和有限等待，命令需要让数据库在授权检查之前取得写锁。

本任务只修复 ownership transfer 的并发安全和其回归测试，不改变交接领域状态机、候选资格、角色语义或 API 错误合同。

## 2. 测试并发设计

主测试线程在启动 worker 前提取所有标量输入：transfer_id、heir_id、heir_account_id、space_id。worker 闭包只捕获这些整数、barrier、results lock 和结果容器；不捕获 `heir`、`transfer` 或主线程 Session。

每个 worker：

1. 创建独立 SessionLocal。
2. 用标量 ID 加载受让人和 account，构造 ActorContext。
3. 在 barrier 上等待，使用 `_SYNC_TIMEOUT`。
4. 调用 `accept_transfer`。
5. 将 `won`、业务 HTTP 状态或普通异常写入受锁保护的结果列表。
6. 在 finally 中关闭 Session。

主线程启动两个 worker 后用带 `_SYNC_TIMEOUT` 的 join；若线程仍存活，立即让测试失败并确保不继续等待。最终使用主测试 Session 重新查询 OwnershipTransfer、FamilySpace 和 SpaceMember，不读取 worker ORM 对象。

## 3. 命令事务设计

将 `accept_transfer` 的 actor 加载和所有 transfer 读取放入：

```python
with command_transaction(session, immediate=True):
    actor = load_actor(session, ctx)
    ...
```

`BEGIN IMMEDIATE` 在 SQLite 中前置取得写锁，使 transfer 状态检查、条件更新和所有权/角色变更在同一写事务中完成。条件 UPDATE 继续以 `status == 'pending'` 为 CAS 门禁；rowcount=1 的线程执行 `_apply_transfer_ownership`，另一个线程按现有业务路径得到 409。

该方案不依赖 Python 进程锁，适用于多个进程/worker，也不改变 `busy_timeout` 和 WAL 的既有数据库配置。若出现真实数据库锁错误，按基础设施异常处理，不转换为“transfer 已被接受”的业务响应。

## 4. 事务内不变量

- actor 必须是 transfer 的目标用户。
- transfer 必须存在、未过期且为 pending 才能 CAS 接受。
- `_apply_transfer_ownership` 继续验证空间、原管理员和 active member。
- 角色降级、角色升级、owner 镜像、领域事件和 audit 与 transfer 状态在同一事务中提交。
- 任一异常回滚整个命令，不留下零管理员或双管理员终态。

## 5. 兼容与回滚

只修改并发测试和 ownership command 的事务边界；不改 schema、不改 API 路径、不改错误码。若测试暴露真实的既有业务竞态，先保留定向失败证据，停止扩大变更范围，回到命令不变量评审。回滚点为恢复普通事务调用和测试前的同步实现，但不得恢复无 timeout 的全量测试门禁作为最终状态。

## 6. 验证设计

- 定向测试单次、连续 20 次和高重复随机顺序运行。
- ownership transfer 全部相关测试，确认过期、错误受让人、重复接受和角色唯一性不回归。
- 后端全量 pytest 直接包含 `test_concurrent_double_accept_single_winner`，不得使用永久 deselect。
- Ruff、format、mypy 和 git diff --check 通过。
