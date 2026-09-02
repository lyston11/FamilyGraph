# 完成 PersonalFamilyView 遗留授权投影与家庭体验闭环

## Goal

收口 `09-01-personal-family-view` 已实现后的遗留授权投影缺口，为 household card、空间限定统计和 notifications 提供服务端最小投影合同，并补齐撤权、空间切换、状态、字段遮罩和推荐边界回归验证。

用户应能在有权访问的 household/lineage 空间中看到一致、可缓存且不会越权的家庭摘要、统计和通知；浏览器只能消费 PersonalFamilyView 或其他明确授权投影，不得回退旧 graph、全局用户列表或无空间统计。

## Background / Confirmed Facts

- PersonalFamilyView 已按 `viewer_account + root_person + space` 隔离，是可重建的授权投影，不是新的事实真源或公共 FamilySpace。
- 已完成任务已经落地 PersonalFamilyView、Bridge、Steward、状态机、前端 API decoder 和 Pinia store，但 `frontend/src/api/household.ts`、`spaceStats.ts`、`notifications.ts` 仍标记服务端合同未落地。
- `frontend/src/views/HouseholdCardView.vue`、`StatsView.vue`、`NotificationsView.vue` 已在 404 时安全降级，并明确不回退 `/users`、旧 members、旧 graph 或旧无空间统计。
- `backend/app/api/misc.py:55-105` 的现有 `/stats` 仍按当前用户可见用户集合返回旧统计，不支持 `space_id`、PersonalFamilyView 版本、空间状态、ETag 或服务端授权聚合合同。
- 前端通知类型已经固定 `read_at`、`domain_status` 和 ActionCard `card_id/revision` 三套独立语义；`kind=action_card` 必须带合法引用。
- 前端各 store 已按 `space_id` 缓存，具有 epoch/requestEpoch、空间切换清理、401/logout 清理和迟到响应丢弃机制。

## Requirements

### PFV-F1：Household card 服务端合同

实现 `GET /api/household-card?space_id=<positive integer>`：

- 仅接受 `family_user` 主体，并从认证身份解析 viewer/account；客户端不能指定其他 viewer/root。
- 重新执行当前空间成员资格、PersonalFamilyView 授权和字段级 VisibilityPolicy 检查。
- 仅返回前端合同需要的 `space_id`、`space_kind="household"`、`space_name`、`view_version`、`computed_at`、`viewer`、安全 `members` 和 `allowed_actions`。
- 不返回 lineage 节点数组、隐藏成员数量、关系图全量边、不可见目标 ID、私人 Memory/Session、附件或敏感档案字段。
- 撤权、空间删除、Bridge 撤销或 policy 收紧后，下一次读取不得继续返回旧投影。
- 支持 `ETag`、`If-None-Match` 和 `304 Not Modified`。

### PFV-F2：空间限定统计合同

扩展为明确的空间投影统计合同 `GET /api/stats?space_id=<positive integer>`，并保持旧调用方不会被静默改变语义：

- 返回 `space_id`、`space_kind`、`status`、`view_version`、`node_count`、`edge_count`、`member_count`、`relation_distribution`、`pending_action_cards`、`pending_memberships`、`computed_at` 和安全的 `stale_reason`。
- 计数必须由服务端按当前授权 PersonalFamilyView/空间投影聚合；前端不得从节点数组推导。
- `none` 节点、不可见边、未授权成员、不可见空间和阻断路径不得计入，也不得用计数反推其存在。
- 状态不是 `current` 时必须显式返回状态；如使用上一份安全快照，必须标明版本和 stale 原因；撤权后不能继续使用已失效快照。
- 支持 `ETag`、`If-None-Match` 和 `304 Not Modified`。

### PFV-F3：Notifications 服务端投影与已读命令

实现：

- `GET /api/notifications?space_id=<positive integer>`
- `POST /api/notifications/{notification_id}/read`
- `POST /api/notifications/read-all`，body 为 `{ "space_id": <id> }`

合同要求：

- 查询严格按 `recipient_account_id + space_id` 过滤，并在返回前再次验证当前授权。
- 返回前端固定的 `NotificationsPage`：`space_id`、安全通知项和服务端计算的 `unread_count`。
- `read_at`、`domain_status`、ActionCard `revision` 必须独立保存和返回；已读操作不能接受、拒绝、执行、撤销或改变任何领域对象。
- `kind="action_card"` 时必须带合法 `card_id/revision`；Bridge 管理员通知只允许最小治理摘要，不得携带家庭敏感数据。
- 单条通知损坏时由前端 decoder 丢弃该条；顶层合同错误由前端整体拒绝。
- 支持 ETag/304，且通知已读后的 ETag 变化只反映已读状态变化。

### PFV-F4：授权、状态和缓存回归

补齐后端和前端回归验证：

- 覆盖 `never_computed`、`queued`、`running`、`current`、`stale`、`failed`。
- `none` 节点完全省略；`masked` 只能作为约定哨兵出现；`lineage_summary` 不能作为新的遍历入口。
- 覆盖撤权、Bridge revoke、空间删除、policy 收紧后的即时隐藏。
- 覆盖空间切换、登出、401、迟到响应和 epoch 隔离，证明旧空间或旧授权数据不能覆盖新状态。
- 覆盖 ActionCard 候选不被渲染为已确认关系，通知已读不改变领域状态。
- 覆盖推荐只消费 `current` PersonalFamilyView，不能反向扩大视图或权限。

### PFV-F5：前端兼容边界

- 继续使用现有 household、spaceStats、notifications decoder/API/store，不在页面或组件内拼装投影。
- 保留端点尚未可用时的安全降级文案，但真实端点就绪后不得继续依赖旧 fallback。
- 不改变旧 `/api/graph/me` 的兼容实现，不把它标记为 PersonalFamilyView 的备用真源。

## Acceptance Criteria

- [ ] Household card 端点返回固定最小字段，执行当前授权复核，拒绝或隐藏撤权后的旧内容，并通过 ETag/304 测试。
- [ ] `stats?space_id=` 返回固定空间统计合同，所有计数由服务端授权聚合，隐藏对象和 none 节点不计入，并通过状态与 ETag/304 测试。
- [ ] Notifications 三个端点按账号和空间隔离；单条已读与全部已读只改变 `read_at`，不改变领域状态或 ActionCard revision。
- [ ] 通知、统计和 household card 的响应均通过字段白名单，不泄露家庭档案、关系图、Memory、Session、附件或未授权对象信息。
- [ ] 撤权、Bridge revoke、policy 收紧、空间切换、401/logout 和迟到响应回归测试通过。
- [ ] 前端页面和 store 只消费服务端授权投影，不回退 `/users`、旧 members、旧 graph 或旧无空间统计。
- [ ] ActionCard 候选、masked/none、stale/failed 状态和推荐 current 边界均有测试。
- [ ] 后端定向测试、Ruff、mypy，以及前端 type-check、lint、test、build 通过。
- [ ] 不引入公共 FamilySpace、推荐触发点、Steward runtime 重构、系统管理员 break-glass 或外部通知渠道。

## Constraints

- 必须遵守 `.trellis/spec/architecture.md` 的四级可见性、PersonalFamilyView、系统管理员和空间角色边界。
- 数据库写入和已读命令必须使用现有短事务/命令边界；schema 变更必须走 Alembic。
- 查询不能把 ORM 实体直接序列化为浏览器合同，必须使用专用 schema 和字段白名单。
- 保留当前工作树中与本任务无关的修改，不覆盖 `09-01-remove-guest-role` 的 WIP。

## Out of Scope

- 新用户推荐的准确触发点、推荐 UI 或推荐算法重做。
- Steward 迁回 Assistant runtime、人物身份去重核心逻辑或旧 graph 语义重写。
- 短信、邮件、推送等外部通知渠道。
- 公共 FamilySpace、多级系统管理员以及系统管理员直接浏览家庭数据。
- PIN 重置、custody transfer、claim dispute、data-rights 等 break-glass 能力。
