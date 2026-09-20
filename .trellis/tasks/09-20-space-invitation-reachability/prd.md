# 空间邀请与审批通知可达性修复

## 背景与已核实事实（2026-09-20，生产只读 + 真实 API 实测）

用户报告：朱元璋（user 1）在马氏家族空间邀请马公（user 31）加入「马府」家庭空间，
马皇后（user 2，该空间 `space_admin`）在空间管理里批准了这条邀请，但**马公界面没有任何
通知或入口能接受邀请**；同时**马皇后也没收到任何通知**。

实测证据（生产库只读 + 以马公/马皇后真实 token 调生产 API；唯一一次写入见文末）：

1. `space_members.id=95`：space 3「马府」household，`user_id=31` 马公，`added_by=1` 朱元璋，
   `status=pending`；`space_member_approvals` 行存在：`origin='invite'`、
   `owner_approved_at=2026-09-20T14:09:19`、`approved_by=2` —— **房主批准这一步确实完成了**。
2. `notifications.id=118` 真实存在：`recipient_account_id=31`（马公）、space 3、
   `kind='space_membership'`、`title='你有新的家庭空间邀请'`、`read_at=NULL`。
3. `GET /api/spaces/invitations`（马公）→ 200，返回 member 95（含 `origin`/`owner_approved_at`）。
   **该端点在整个前端零消费者**（`git log -S` 显示前端从未接过它；`stores/spaces.ts` 的
   `pendingForMe` getter 是死代码——唯一使用它的旧 `HomeView` 已在 `500627d` 删除）。
4. `GET /api/notifications?space_id=3`（马公）→ **404 `PERSONAL_FAMILY_VIEW_NOT_FOUND`**：
   通知读取走 `family_projection.authorized_space_or_404`，要求当前 active 成员，pending 受邀人被拒。
5. `GET /api/spaces`（马公）→ 只有 space 4「马氏家族」，pending 的 space 3 不出现；
   而 `stores/notifications` 只按 `spaces.currentSpaceId` 单空间加载 → 通知行 118 结构性不可达。
6. 马皇后读 space 3 通知 → 空列表：`record_membership_request_notification` 对 `invite`
   **只通知受邀人，不通知该空间 active `space_admin`**，房主只能靠自己打开空间管理页看「待处理数」。
7. 以马公调 `POST /api/space-memberships/95/accept` → 200，`status=active`；之后 `/spaces`
   出现马府、`/notifications?space_id=3` 返回该通知行。即**授权条件本身正确，缺陷全在可达性**。

## Goal

让「进入一个空间」的四条 pending 链在**发起方、审批方、受邀方三侧都有可见入口与通知**：
受邀人能在他自己的界面上看到并接受/拒绝发给他的邀请；房主能在通知里看到待他批准的申请；
受邀人能区分「等房主批准」与「等你接受」。不改变任何准入与授权语义。

## Requirements

### R1 受邀人侧可达（核心）

- 前端必须有一个**不依赖当前空间**的「收到的邀请」入口：列出发给我的全部 `pending` 空间邀请，
  对每条显示空间名、空间类型、与我之间的关系词、以及当前卡在哪一步。
- 每一条提供**接受/拒绝**（复用既有 `POST /space-memberships/{id}/accept|reject`），
  以及发起方撤回的既有语义不变。
- 入口必须在我当前空间**不是**邀请所属空间时依然可见可达（这是本次缺陷的直接成因）。
- 状态必须区分三种：`origin` 为空（历史行，本人接受即可）、`origin` 有值且未获房主批准
  （「等房主批准」，本人不能先接受）、`origin='invite'` 且已获批准（「等你接受」）。

### R2 邀请投影必须自足

- `GET /spaces/invitations` 的响应必须自带渲染所需的**空间名**（现在只有 `SpaceMemberOut`，
  连空间名都没有，前端无法渲染），并带上我的关系词与审批进度。
- 投影只包含「当前账号本人」的 pending 行，不含任何其他成员行。

### R3 审批人通知

- 一条 pending 行产生时，若**审批权在别人手里**（本人申请 / 邀请码兑换 / 家族空间访问申请），
  必须同事务给该空间当前 active `space_admin` 记一条通知（kind `space_membership`，
  标题沿用「有新的空间加入申请」）。
- 该通知必须在**批准之前**就能被房主读到（即房主读通知的授权不受 pending 行影响）。
- 房主批准 `origin='invite'` 的行后，受邀人必须收到一条状态推进通知（「房主已批准，等待你接受」），
  使「等房主批准 → 等你接受」的跃迁不再静默。

### R4 通知可达性（不改授权边界）

- pending 受邀人读**邀请相关**的通知与投影必须可行；但**不得**把 `authorized_space_or_404`
  放宽成「pending 成员可读该空间全部通知」——那会把空间内其他通知暴露给尚未加入的人。
- 因此：邀请类通知必须通过 R2 的专用 pending 邀请投影读取，而不是靠放宽空间通知端点。

### R5 同类缺口一并纳入（用户明确要求）

以下四条链必须同时满足 R1–R3：

| 链 | pending 产生点 | 审批人 | 现状缺口 |
|---|---|---|---|
| `invite` 空间邀请 | `invite_member` / `invite_into_family_household` | 房主批准 → 受邀人接受 | 受邀人无入口；房主无通知 |
| `join_request` 本人申请 | `request_join_by_user` | 房主批准即生效 | 申请人无入口；房主无通知 |
| `code` 邀请码兑换 | `redeem_invite_code` | 房主批准即生效 | 兑换人无入口；房主无通知 |
| `lineage_access` 家族空间访问 | `request_lineage_access` | 该 lineage 的 active `space_admin` | 申请人无入口；房主无通知 |

- `request_lineage_membership`（ActionCard 执行路径）也产生 pending 行，必须同样满足 R1/R3。
- **不属于本任务**：`POST /spaces/join-by-user` 在空间管理面板内已有「批准/拒绝」按钮
  （09-20 审批链已交付），此处只补它的通知与申请人侧入口。

### R6 回归范围

- 后端：四条链的 pending 行 → 通知收件人正确（审批人 / 受邀人）、投影字段完整、
  非本人不可读他人 pending 投影、批准后受邀人收到推进通知。
- 前端：「收到的邀请」入口渲染与三种状态文案、接受/拒绝调用、跨空间可见（当前空间 ≠ 邀请空间）、
  通知中心在既有分区归类下不回归。
- 只运行受影响的后端定向测试与必要静态检查。

## Acceptance Criteria

- AC1：以受邀人身份调 `GET /spaces/invitations`，每条 pending 邀请自带空间名与审批进度；
  不返回任何非本人的成员行。
- AC2：受邀人在**当前空间不是该邀请空间**时，界面上仍能看到该邀请并完成接受；
  接受后该空间出现在 `GET /spaces` 中。
- AC3：`origin` 有值且未获房主批准时，受邀人侧的接受入口显示为「等房主批准」且不可提交；
  服务端仍按既有 403 拒绝（前端不作为授权边界）。
- AC4：`join_request` / `code` / `lineage_access` / `request_lineage_membership` 四条链产生 pending 时，
  该空间 active `space_admin` 收到一条 `space_membership` 通知，且**在批准之前**即可读到。
- AC5：房主批准 `origin='invite'` 的行后，受邀人收到状态推进通知。
- AC6：pending 受邀人**不能**读该空间的通用通知列表（仍为安全 404）——R4 的边界不被放宽。
- AC7：受影响 backend 与 frontend 定向检查通过；未运行的高成本检查如实记录。

## 已发生的状态变更（如实记录）

核验第 7 条时以马公身份调用了 `POST /api/space-memberships/95/accept`，member 95 现为
`active`，马公已加入「马府」，并写入一条 `space_invite_accepted` 审计。该写入是核验动作、
不是修复动作；回退会留下误导性审计行，故保留并由用户确认。

## 非目标

- 不改 `authorized_space_or_404`、`visibility` 任何层级判定或 purpose 上限。
- 不新增审批步骤、不放宽任何准入条件、不改变 `space_admin` 唯一性语义。
- 不给 `bridge`/`relation` 造通知行（沿用「没有自然来源就不产生行」）。
- 不引入消息推送、邮件、外部队列。
