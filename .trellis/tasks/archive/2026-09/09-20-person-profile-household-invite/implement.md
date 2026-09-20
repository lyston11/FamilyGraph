# 实施计划

## 规划与启动

- [x] 核对既有能力：邀请写命令、退出命令、空间列表投影、公示页与设置页结构。
- [x] 确认边界：不新增写命令、不新增迁移、不改既有授权判定；只补用户入口与一个只读聚合端点。
- [x] 用户已确认：邀请时**弹窗选择空间**；本次同时新增退出家庭空间/家族空间入口并二次确认。
- [ ] 用户批准规划后 `task.py start`，进入隔离 worktree。

## 实现顺序

1. 后端只读聚合：`GET /api/spaces/household-invite-options?target_user_id=`（我的 household × 目标状态 active/pending/none；目标不可见 → 404）。实现为 `commands/spaces.py` 只读函数 + `api/spaces.py` 序列化。
2. `SpaceOut` 增可选 `my_member_id`，在 `list_my_spaces` 填入；其他构造点保持 None。
3. 前端 API：`fetchHouseholdInviteOptions`；`types/api.ts` 增 `HouseholdInviteOption` 与 `my_member_id`。
4. 新组件 `InviteToHouseholdDialog.vue`：空间选择 + 三态禁用 + 确认调用（显式 spaceId）。
5. `PersonProfileView.vue` 增「邀请加入家庭空间」分区与入口（仅目标可见且有 household 时）。
6. `SettingsView.vue` 增「我的空间」分区：列出 active 空间 + 退出按钮 + `useDialog().warning` 二次确认 + `SPACE_MANAGER_TRANSFER_REQUIRED` 可读提示。
7. `useSpaceContext.ts` 增 `leaveSpace(memberId)`：调用退出命令 → `spaces.load()` → 若退出的是当前空间则 `clearSpaceCaches` 并切到剩余第一个空间（无剩余则 `currentSpaceId = null` 走空态）。
8. 回归测试：
   - 后端 `tests/test_household_invite_options.py`：三态、只返回我的 household、目标不可见 404、非成员不返回；退出命令的成功/非成员/需先交接/不得代他人退出。
   - 前端 `person-profile.spec.ts` 增邀请入口三态与调用参数；新 `InviteToHouseholdDialog.spec.ts`；`settings.spec.ts` 增退出二次确认（取消零写入/确认调用）与管理员交接提示；`useSpaceContext.spec.ts` 增退出后上下文切换。
9. 更新 09-01 归档任务中「不提供加入空间按钮」的说明与对应断言（本次需求显式修订该红线）。

## 验证

- `cd backend && .venv/bin/python -m pytest -q tests/test_household_invite_options.py tests/test_space_invite_authz.py tests/test_space_access_boundary.py tests/test_m2c_flows.py`
- `cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app`
- `cd frontend && npm run lint && npm run type-check && npm test && npm run build`
- 涉及 API 合同变更（新增端点 + `SpaceOut` 字段），按 frontend 规范需补跑
  `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`；环境阻塞（退出码 2）如实记录。
- 375px 视口人工过一遍新弹窗与设置分区（双主题）。

## 回滚点

先回退前端入口（隐藏按钮/分区），再回退只读端点与 `my_member_id`；无数据修复、无迁移需要回滚。
