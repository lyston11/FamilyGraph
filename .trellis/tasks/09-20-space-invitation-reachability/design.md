# 技术设计：邀请与审批的可达性

## 1. 缺陷根因（三条，互不重叠）

| # | 层 | 缺陷 | 证据 |
|---|---|---|---|
| D1 | 前端 | `GET /spaces/invitations`（发给我的 pending）**零消费者**；`POST /space-memberships/{id}/accept` 全前端只有一个调用点 `SpaceGovernancePanel`，且只对 `isSelfRequested` 的行渲染 → 受邀人无入口 | `grep` 全前端无引用；`stores/spaces.ts::pendingForMe` 为死代码（唯一使用它的旧 `HomeView` 于 `500627d` 删除） |
| D2 | 通知生成 | `record_membership_request_notification` 只按 `user_id != added_by` 二分：`invite` 只通知受邀人（房主无通知）；`code` 通知的是**兑换人**而非审批人，且标题是「你有新的家庭空间邀请」（误导） | 生产 `notifications` 中 space 3 只有一条发给 user 31 的行，马皇后侧为空 |
| D3 | 可达性 | 通知按 `space_id` 读取且要求 active 成员（`authorized_space_or_404`），前端只按 `spaces.currentSpaceId` 单空间加载 → pending 受邀人的通知行结构性不可达（404） | 马公 `GET /api/notifications?space_id=3` → 404；`GET /api/spaces` 不含 space 3 |

关键结论：**授权条件本身是正确的**（马公 accept 返回 200，之后一切可见）。缺陷全在「入口缺失 + 通知收件人错误 + 跨空间不可达」，因此修复不触碰任何准入或可见性判定。

## 2. 边界与不变量（不可放宽）

- `family_projection.authorized_space_or_404` **不改**：pending 受邀人读该空间通用通知仍是安全 404。
  否则会把空间内全部通知（含他人 action_card / bridge）暴露给尚未加入的人。
- 邀请侧的可达性由**专用 pending 投影**承载（R2），不靠放宽空间通知端点。
- `visibility.evaluate` 的层级、purpose 上限、`_pending_membership_link` 最小互见规则**不改**。
- 审批顺序语义（`space_fsm.transition` / `approve_pending_membership`）**不改**。
- 不新增 notification kind → **不需要迁移**。

## 3. 后端

### 3.1 pending 邀请投影（自足、只含本人）

新 schema `PendingInvitationOut`（`app/schemas/space.py`，`extra="forbid"`）：

```
id: int                     # SpaceMember.id（接受/拒绝的入参）
space_id: int
space_name: str
space_kind: Literal["household", "lineage"]
direction: Literal["incoming", "outgoing"]
   incoming = 别人邀请我（origin='invite'，或历史行 added_by != 我）
   outgoing = 我发起的加入（origin ∈ {join_request, code}，或历史行 added_by == 我）
stage: Literal["awaiting_owner", "awaiting_me"]
   awaiting_owner = 房主还没批准（我此时不能接受）
   awaiting_me    = 等我接受
counterpart_user_id: int | None     # 对方（邀请人 / 我申请的落点）
counterpart_name: str | None        # 仅当对我可见时给出名字，否则 null（不脱敏成哨兵）
relation_label: str | None          # 我与对方之间的标注词（取不到则 null，不编造）
owner_approved_at: datetime | None
updated_at: datetime
```

`stage` / `direction` 的判定与 `space_fsm` 完全同源，不在前端二次推导：

```
approval = space_fsm.approval_for(session, member.id)
approved = approval is not None and approval.owner_approved_at is not None
if approval is not None:
    direction = "incoming" if approval.origin == "invite" else "outgoing"
    stage = ("awaiting_me" if approved else "awaiting_owner") \
        if approval.origin == "invite" else "awaiting_owner"
else:                       # 历史行（无审批行）：沿用旧语义
    outgoing = member.added_by == member.user_id
    direction = "outgoing" if outgoing else "incoming"
    stage = "awaiting_owner" if outgoing else "awaiting_me"
```

端点 `GET /spaces/invitations` 改为 `response_model=list[PendingInvitationOut]`：

- 过滤：`SpaceMember.user_id == actor.id` 且 `space_fsm.effective_status(m) == "pending"`
  （惰性过期行不再作为可操作邀请返回）；按 `updated_at desc` 排序。
- 空间名来自 `FamilySpace.name`；`counterpart_name` 走
  `visibility.evaluate(..., space_context=space.id, purpose=PURPOSE_PROFILE).visible`
  的既有投影（与 `notifications._project_item` 同一口径），不可见即 `null`。
- `relation_label`：取 `member_labels.pair_for(space_id, me, counterpart)`。
  `join_request` 的对方（我申请的那个 target）不落在成员行上，取不到时返回 `null`——
  宁可缺字段也不猜一对标注。
- **破坏性变更说明**：该端点的 `response_model` 由 `SpaceMemberOut` 变为 `PendingInvitationOut`。
  已核实全仓（frontend / system-admin-frontend / backend tests / smoke）**无任何消费者**，
  故不保留旧形状。

### 3.2 通知收件人修正（`services/notifications.py`）

`record_membership_request_notification` 由「二分」改为「按审批进度分两条独立通知」：

1. **受邀人**：`member.user_id != member.added_by` 且（无审批行 或 `origin == 'invite'`）
   → 通知 `member.user_id`，标题「你有新的家庭空间邀请」。
   该条件显式排除 `origin='code'`：兑换人不是被邀请人，不该收到邀请文案。
2. **审批人**：`needs_owner = (not approved) and (approval is not None or added_by == user_id)`，
   且 `manager = active_space_manager(space.id)` 非空、`manager.user_id != member.user_id`
   → 通知该 manager，标题「有新的空间加入申请」。
   末尾的排除项防「给自己发通知」；**房主自己发出的邀请仍然通知房主**——该行确实卡在
   他这一步，不提醒就会静默停在 pending（只有「被批准的人就是房主自己」不发，他永远不能自批）。

逐链核对（括号内为修复前行为）：

| 链 | 受邀人通知 | 审批人通知 |
|---|---|---|
| `invite`（他人邀请） | ✓（✓） | ✓（**✗**） |
| `invite`（房主自己邀请） | ✓（✓） | ✓（**✗**） |
| `join_request` | —（—） | ✓（✓） |
| `code` | 抑制（**误发给兑换人**） | ✓（**✗**） |
| `lineage_access` / `request_lineage_membership`（无审批行、自申请） | —（—） | ✓（✓） |

### 3.3 审批推进通知

`commands/spaces.approve_membership` 在 `approve_pending_membership` 之后：若
`approval.origin == 'invite'` 且 `member.status` 仍为 `pending`，同事务给 `member.user_id`
记一条通知，标题「邀请已获房主批准，等待你接受」（新常量 `_INVITE_APPROVED_TITLE`）。
理由：`awaiting_owner → awaiting_me` 的跃迁此前完全静默，受邀人即使看到了邀请也不知道
球已经在他这边。`join_request`/`code` 批准即 active，不需要这条。

### 3.4 不改的部分

- `space_fsm`（invite / transition / approve_pending_membership）零改动。
- `list_my_spaces` 不返回 pending 空间（那是产品语义：pending 不算「我的空间」），
  pending 可见性由 3.1 的专用投影承担。
- 无新迁移、无新 notification kind、无新错误码。

## 4. 前端

### 4.1 数据层

- `types/api.ts`：新增 `PendingInvitation`（与后端字段一一对应）。
- `api/spaces.ts`：新增 `fetchMyInvitations()`（`GET /spaces/invitations`）。
- `stores/spaces.ts`：新增 state `invitations: PendingInvitation[]`、
  action `loadInvitations()`（世代校验同既有模式，迟到响应不覆盖新会话）、
  action `resolveInvitation(memberId, action)`（调既有 `resolveMembership`，
  成功后 `loadInvitations()`；接受时再 `load()` 让新空间进入我的空间列表）、
  getter `invitationsAwaitingMe`（`stage === 'awaiting_me'` 的条数，供角标）。

### 4.2 入口与视图

- 新视图 `views/InvitationsView.vue` + 路由 `/invitations`（name `invitations`）。
  两段：**待我接受**（`direction=incoming`，`awaiting_me` 可接受/拒绝；
  `awaiting_owner` 显示「等待房主批准」且按钮禁用）与**我发起的申请**
  （`direction=outgoing`，只读展示进度，可撤回）。
  空态、加载失败分类文案沿用 `api/loadError.describeLoadError` 与既有页面模式。
- 壳层入口：`AppShell` 账号菜单新增「收到的邀请」，带 `invitationsAwaitingMe` 角标；
  该入口**与当前空间无关**，这正是本次缺陷的直接成因所在。
- 壳层在 `auth.isLoggedIn` 时调用 `loadInvitations()`（一次），使角标在任何页面都成立。

### 4.3 授权不作为前端边界

前端只把 `stage` 映射为可点/禁用；真正的拒绝仍是服务端的 403/409。
`awaiting_owner` 的接受按钮禁用，但即便被绕过，服务端仍按既有 `space_fsm` 拒绝。

## 5. 兼容、回退与风险

- **兼容**：不新增数据库列/表，不需要数据修复；既有通知行保持可读。
- **回退顺序**：前端入口 → 3.3 推进通知 → 3.2 收件人修正 → 3.1 端点投影。
  3.1 是破坏性接口变更，回退时前端必须同时回退（该端点原本无人消费，回退风险低）。
- **风险**：3.2 会为一次邀请产生两条通知（受邀人 + 房主），通知数增加是预期行为；
  已用「manager == member.user_id 不通知」与「`origin='code'` 不发邀请文案」两条规则限制噪音面。
- **不做**：不放宽空间通知授权；不新增推送渠道；不给 `bridge`/`relation` 造行。

## 6. 验证入口

- 后端：`tests/test_invitation_reachability.py`（新增）、`tests/test_notifications.py`、
  `tests/test_member_approval_and_labels.py`、`tests/test_space_access_boundary.py`、
  `tests/test_family_space_join_invite.py`、`tests/test_invite_codes_api.py`、`tests/test_m2c_flows.py`。
- 前端：`views/__tests__/invitations.spec.ts`（新增）、`views/__tests__/notifications.spec.ts`、
  `components/shell/__tests__/AppShell.spec.ts`、`views/__tests__/settings.spec.ts`。
- 静态：backend `ruff check` / `ruff format --check` / `mypy app`；
  frontend `lint` / `type-check` / `test` / `build`。
