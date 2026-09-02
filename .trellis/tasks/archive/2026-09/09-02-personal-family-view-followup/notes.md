# PersonalFamilyView 遗留授权投影闭环 — 实施记录

## 实施结果（2026-09-03）

任务主体实现已随提交 `aabb80f` 入库（household card / space stats / notifications
的服务端合同、迁移 0027、路由注册、生成钩子），本次会话补齐缺口并完成全量验证。

### 本次新增/修改

- `backend/tests/test_notifications.py`：通知合同测试 9 例（此前通知实现无测试）。
  覆盖：action_card/space_membership 自然来源生成、收件人隔离与安全 404、
  字段白名单（item/payload 键集合精确断言）、actor 不可见时 `{"__masked__": true}`
  哨兵、单条已读只改 read_at（ActionCard state/revision 断言不变、幂等）、
  read-all 账号+空间隔离、ETag/304 与撤权后授权先于 304、损坏 action_card 引用
  fail-closed 丢弃、domain_status 实时投影（接受邀请后 pending → active）。
- `backend/app/api/misc.py`：修正 `_space_stats`/`stats` 返回类型注解（mypy）。
- ruff --fix + format：清理 aabb80f 遗留的 14 个 lint 错误与 8 个未格式化文件
  （含 UP012、W292、E501），现全部通过。

### 关键行为确认（与冻结前端合同对齐）

- 通知列表先做空间授权复核：pending 受邀人读取该空间通知为安全 404，接受邀请
  成为 active 成员后才可见（授权先于内容，测试已锁定该语义）。
- 通知生成只挂真实领域事件：`action_cards.create_card` 与
  `space_fsm` pending 成员行创建；bridge/relation 无自然来源不产生行。
- 旧无空间 `/stats` 合同原样保留（无 ETag），`space_id` 查询参数进入新的
  SpaceStatsOut 合同；两合同互不回退。
- household card 仅 household 空间可用；lineage 空间走同一安全 404。

### 验证结果

- 后端：pytest 全量 **659 passed / 3 skipped**（含 ownership transfer 并发用例，
  无需 deselect）；ruff check/format 通过；mypy 142 文件无错误。
- 前端：type-check / lint 通过；**60 文件 490 tests passed**；build 成功。
- 迁移：临时 DATA_DIR 执行 `upgrade head → downgrade 0026 → upgrade head` 通过
  （0027_notifications 含 CHECK/索引/FK 完整重建与 downgrade）。

### 遗留说明

- 前端 household/spaceStats/notifications decoder 与 store 无需改动：服务端载荷
  与 `frontend/src/api/*.ts` 冻结 decoder 逐字段对齐（含 Maskable 哨兵形态）。
- join-by-user 的可见性前置（申请人对目标用户可见）属既有产品语义，测试通过
  created_by 代管链接构造，未改变产品代码。
