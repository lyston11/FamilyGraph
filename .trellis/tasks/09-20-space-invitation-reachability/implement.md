# 实施计划

## 规划与启动

- [x] 核对现状：三条根因（D1 入口缺失 / D2 收件人错误 / D3 跨空间不可达）已在生产只读 + 真实 API 上核实（见 prd.md 背景）。
- [x] 用户批准：全量修复，同类缺口一并纳入（`join_request` / `code` / `lineage_access` / `request_lineage_membership`）。
- [ ] `task.py start` → 隔离 worktree，后续所有改动只在该 worktree 内提交。

## 实现顺序（后端 → 前端 → 测试）

1. **后端投影**（`schemas/space.py` + `api/spaces.py`）
   - 新增 `PendingInvitationOut`；`GET /spaces/invitations` 改用新投影。
   - 判定 `direction` / `stage` 全部复用 `space_fsm.approval_for` + 历史行语义，不在路由层重写 FSM。
   - `counterpart_name` 走既有 `visibility.evaluate(PURPOSE_PROFILE)`；不可见给 `null`（不用 masked 哨兵）。
   - `relation_label` 走 `member_labels.pair_for`；取不到给 `null`。
   - [x] 用户确认：邀请列表**显示对方名字**（保持现状，不改为中性占位）。
2. **后端通知收件人**（`services/notifications.py`）
   - `record_membership_request_notification` 拆成「受邀人通知」+「审批人通知」两条独立判定。
   - 抑制规则：`origin='code'` 不发邀请文案；`manager == member.user_id` 不发审批人通知。
     **房主自己发出的邀请仍通知房主**（该行确实卡在他这一步）。
3. **后端审批推进通知**（`commands/spaces.py::approve_membership`）
   - `origin='invite'` 且仍 pending 时，给受邀人记「邀请已获房主批准，等待你接受」。
4. **后端回归测试**（新增 `tests/test_invitation_reachability.py`）
   - 四条链的通知收件人矩阵；投影字段与 `stage`/`direction` 三态；非本人不可读他人投影；
     房主批准前即可读到待批准通知；批准后受邀人收到推进通知；
     pending 受邀人读该空间通用通知仍为 404（R4 边界不被放宽）。
5. **前端数据层**（`types/api.ts` / `api/spaces.ts` / `stores/spaces.ts`）
   - `PendingInvitation` 类型、`fetchMyInvitations()`、`invitations` state + `loadInvitations()` +
     `resolveInvitation()` + `invitationsAwaitingMe` getter（带世代校验，沿用既有模式）。
6. **前端入口与视图**（`views/InvitationsView.vue` + 路由 + `AppShell`）
   - 两段式（待我接受 / 我发起的申请）；`awaiting_owner` 显示「等待房主批准」且禁用；
     接受/拒绝走 `resolveInvitation`；空态与失败分类文案沿用既有模式。
   - 账号菜单「收到的邀请」入口 + 角标，与当前空间解耦。
7. **前端回归测试**
   - `views/__tests__/invitations.spec.ts`：三态文案与按钮禁用、接受调用、跨空间可见（当前空间 ≠ 邀请空间）、空态。
   - 既有 `notifications.spec.ts` / `AppShell.spec.ts` / `settings.spec.ts` 保持通过（新增调用需打桩）。
8. **Spec 更新**
   - `architecture/4--4-ad-4.md`：补充「pending 邀请投影（`PendingInvitationOut`）」与
     「通知收件人矩阵 + 审批推进通知」两段合同。
   - `frontend/state-management.md`：补充邀请 store 的跨空间加载与角标约定。

## 已确认的边界（用户裁决，2026-09-20）

- 邀请列表**显示对方名字**（保持 `visibility` 既有口径，不改为中性占位）。
- 「空间管理 → 邀请成员」**不要求同族**：保持既有设计（对方本人接受 + 房主批准双重把关），
  本任务不收紧。
- 由此确认：本任务不修改 `visibility`、不修改 `invite_member` 的准入条件。

## 验证

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_invitation_reachability.py tests/test_notifications.py \
  tests/test_member_approval_and_labels.py tests/test_space_access_boundary.py \
  tests/test_family_space_join_invite.py tests/test_invite_codes_api.py tests/test_m2c_flows.py
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app
cd frontend && npm run lint && npm run type-check && npm test
```

高成本检查（`npm run build`、`frontend-api-smoke.sh`）在提交说明中如实记录是否运行。

## 回滚点

1. 前端入口（视图 + 路由 + 菜单项 + store action）
2. 审批推进通知（3.3）
3. 通知收件人修正（3.2）
4. 端点投影（3.1，破坏性；原本无人消费，回退风险低）

无数据库迁移，回滚不需要数据修复。
