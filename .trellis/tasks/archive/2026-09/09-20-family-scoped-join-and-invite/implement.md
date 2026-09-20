# 实施计划

## 规划与启动

- [x] 核对既有能力：`join-by-user` 命令、`invite_member` 命令、`lineageForSpace`/`lineage_space_id` 配对、邀请码途径。
- [x] 用户确认：两个方向都限定当前家族空间；同族是前提；不同族只能走邀请码。
- [ ] 用户批准规划后 `task.py start`，进入隔离 worktree。

## 实现顺序

1. 后端只读投影：`GET /api/spaces/family-space-options?lineage_space_id=&target_user_id=`，返回 `shares_lineage` + `invite[]` + `join[]`；移除 `household-invite-options` 端点与 schema。
2. 后端邀请：新增 `commands.spaces.invite_into_family_household`（同族 + 空间归属校验，复用 `space_fsm.invite` 与既有事件/审计形状）；API 端点 `POST /spaces/family-invitations`。既有 `invite_member` 不动（空间治理面板与邀请码路径不受影响）。
3. 后端申请：`request_join_by_user` 改为按 `lineage_space_id` 定位对方的家庭空间（移除全局 owner 回退）；`POST /spaces/join-by-user` 增加必填 `lineage_space_id` 与可选 `space_id`，校验空间归属与同族。
4. 前端 API/types：`fetchFamilySpaceOptions`、`inviteIntoFamilyHousehold`、`joinByUser(lineageSpaceId, targetUserId, spaceId?)`；移除 `fetchHouseholdInviteOptions`。
5. 前端弹窗：`InviteToHouseholdDialog` 改为双向分组（邀请/申请），状态与禁用、按钮文案随选择变化；不同族时显示邀请码途径提示。
6. 前端页面：`PersonProfileView` 解析当前 lineage（lineage 直接用；household 用 `lineageForSpace` 配对），解析不出则不显示入口。
7. 回归测试：
   - 后端 `tests/test_family_space_join_invite.py`：同族前提（不同族两个方向都拒绝）、空间归属（篡改为别的家族的空间 → 拒绝、不落 pending）、双向三态、邀请需对方接受、申请需管理员批准且不得自批。
   - 前端 `person-profile.spec.ts`：双向分组与状态、按钮文案、不同族时提示邀请码且无按钮、调用参数带 lineage id。
8. 更新 `tests/test_household_invite_options.py`（保留退出空间部分，移除已被替换的邀请选择断言）与 `tests/test_m2c_flows.py` 的 `join-by-user` 调用。
9. 更新 spec `architecture/4--4-ad-4.md` 的 join_request 段（家族限定 + 双向）。

## 验证

- `cd backend && .venv/bin/python -m pytest -q tests/test_family_space_join_invite.py tests/test_household_invite_options.py tests/test_m2c_flows.py tests/test_space_access_boundary.py tests/test_space_invite_authz.py`
- `cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app`
- `cd frontend && npm run lint && npm run type-check && npm test && npm run build`
- `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`（端点合同变更，需真实链路证据；退出码 2 按环境阻塞如实记录）。
- 生产只读核对：同族/不同族两个方向的实际响应，以及演示数据下「当前家族空间 → 其家庭空间」的解析结果。

## 回滚点

先回退前端入口，再回退两个端点与命令；无迁移、无数据修复需要回滚。
