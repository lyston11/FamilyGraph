# 技术设计：公示页邀请入家庭空间 + 退出空间

## 1. 边界与既有能力

两条需求都不新增授权模型，只补齐缺失的用户入口：

- **邀请**：既有 `POST /spaces/{space_id}/members`（`commands.spaces.invite_member` → `space_fsm.invite`）已实现「active 成员邀请 → pending 成员行，受邀人本人接受后才 active」。缺的只是「在某人公示页挑一个我的家庭空间」这个入口，以及「目标在该空间已是什么状态」的读取。
- **退出**：既有 `DELETE /space-memberships/{member_id}`（`commands.spaces.leave_or_remove_membership` → `space_fsm.transition`）已实现「本人退出 active / 发起方撤回 pending」，并且已对 `role='space_admin'` 返回 409 `SPACE_MANAGER_TRANSFER_REQUIRED`。缺的只是「列出我的空间 + 二次确认 + 退出后修上下文」的入口。

因此本次不新增写命令、不新增迁移、不改变任何既有授权判定。

## 2. 邀请状态读取（唯一新增读端点）

公示页要在打开弹窗时就知道「目标在我每个家庭空间里是什么状态」，逐个 `GET /spaces/{id}/members` 会产生 N 次请求。新增一个只读端点：

```
GET /api/spaces/household-invite-options?target_user_id=<id>
→ [{ space_id, space_name, target_status: 'active' | 'pending' | 'none' }]
```

- 只返回**我**（调用者）为 active 成员、且 `kind='household'` 的空间；按空间 id 稳定排序。
- `target_status` 只区分三种：`active`（已是成员）、`pending`（已有待处理邀请）、其余（含 `rejected/withdrawn/removed`）一律 `none`——终态行可以再次邀请，与 `space_fsm.invite` 的复活语义一致。
- 授权：调用者必须是该空间的 active 成员（由「我的空间」定义天然成立，仍显式复核）；`target_user_id` 必须对调用者可见（`visibility.evaluate`），否则与不可见目标同一 404——与 `join-by-user` / `lineage-access-requests` 同形状，不把本端点变成存在性探针。
- 只读：不写库、不产生通知、不创建 pending 行。
- 放在 `commands.spaces` 还是 API 层？本端点是纯读取聚合，实现为 `commands/spaces.py` 的只读函数（与 `request_*` 同文件、便于复用 `_space_or_404`/成员判定），API 层只做序列化。

## 3. 退出入口的成员 ID

设置页要退出某个空间，需要**我**在该空间的 `SpaceMember.id`。既有 `GET /spaces` 已按空间查过我的成员行（`memberships_by_space`），但没把 id 暴露出来。给 `SpaceOut` 加一个可选字段：

```
my_member_id: int | None = None
```

在 `list_my_spaces` 里填当前账号在该空间的成员行 id。这样设置页可以复用既有 `DELETE /space-memberships/{member_id}`，不必新增「按空间退出」端点，也不会让前端去猜 id。

（`space-management-bootstrap` 的 `SpaceOut` 不填该字段，保持 None；它不是退出入口的数据源。）

## 4. 前端结构

### 4.1 邀请弹窗（新组件）

`frontend/src/components/member/InviteToHouseholdDialog.vue`：

- props：`visible`、`targetUserId`、`targetName`；emit：`update:visible`、`invited`。
- 打开时经 `api/spaces.ts` 的 `fetchHouseholdInviteOptions(targetUserId)` 拉取选项；loading / error / 空 三态。
- 列表逐项显示空间名 + 状态：
  - `active` → 「已在同一家庭空间中」，`disabled`（用 NSelect/NRadio 的 options `disabled` 字段，不是模板属性）；
  - `pending` → 「已发出邀请，等待对方接受」，`disabled`；
  - `none` → 可选。
- 全部为 `active`/`pending` 时显示「该成员已在你全部的家庭空间中」，确认按钮禁用。
- 确认 → `spaces.inviteToSpace(spaceId, targetUserId)`（**带显式 spaceId**，不复用只作用于 `currentSpace` 的 `spaces.invite`）→ 成功 toast「邀请已发送」并关闭；失败显示服务端可读文案。
- 调用方（公示页）负责入口可见性；组件不重复授权判定，也不做本地角色推断。

### 4.2 公示页入口

`PersonProfileView.vue` 在关系上下文之后新增一个分区：

- 仅在 `profileNode !== null`（目标可见）且 `spaces.spaces` 含至少一个 `kind==='household'` 时渲染入口按钮。
- 点击打开 `InviteToHouseholdDialog`。
- 本页仍然不提供「查看对方家庭 / 修改对方资料 / 扩大权限」；本次新增的只有「邀请加入我的家庭空间」。
- 09-01 任务里「不提供加入空间按钮」的旧红线由本次需求显式修订，同步更新该任务归档说明与对应测试断言。

### 4.3 设置页「我的空间」分区

`SettingsView.vue` 新增 `spaces` 分区（tab 标签「我的空间」）：

- 列出 `spaces.spaces`（服务端 `GET /spaces` 真源，仅 active），每行显示名称、类型徽章（家庭空间/族谱空间）、我的角色。
- 每行「退出」按钮 → `useDialog().warning` 二次确认，内容含空间名与后果（退出后失去该空间的数据访问，需要重新申请才能再加入）。
- 取消：零写入（dialog 的 `onPositiveClick` 才调用命令）。
- 确认：调用新的 store action（见 4.4）。
- `space_admin` 行同样显示按钮（不隐藏、不本地预判），由服务端 409 `SPACE_MANAGER_TRANSFER_REQUIRED` 决定结果，前端把它映射为「请先完成空间管理员交接」的可读提示。

### 4.4 store 与上下文修复

`stores/spaces.ts` 新增两个 action：

- `inviteToSpace(spaceId, userId)`：调用 `inviteToSpace(spaceId, userId)` API（不依赖 `currentSpace`），成功不重载当前空间成员（目标不在当前空间上下文里）。
- `leaveSpace(memberId)`：
  1. `removeOrWithdrawMembership(memberId)`；
  2. `await this.load()` 重载空间列表（服务端真源）；
  3. 若刚退出的空间是 `currentSpaceId`：`clearSpaceCaches(previousSpaceId)`（由 `useSpaceContext` 暴露或 store 内既有清理），并切到列表中的第一个空间；列表为空则 `currentSpaceId = null` 走既有空态。
- 不做乐观更新：列表与成员一律以服务端响应为准。

## 5. 兼容与安全

- 不新增/修改写命令与 FSM；退出继续复用既有 `remove` 终态语义，不新增「撤销退出」。
- 邀请继续复用既有 `invite`：pending 需受邀人本人接受，绝不静默加入。
- 不扩大任何读取：新读端点只返回**我自己**空间内、且对我可见的目标的状态。
- 不在前端做授权推断：入口可见性只是 UX，服务端仍是唯一授权边界。
- 无数据库迁移。

## 6. 回滚

前端入口可单独回退（隐藏按钮/分区）；后端 `household-invite-options` 与 `my_member_id` 都是只读新增，回退不涉及数据修复。
