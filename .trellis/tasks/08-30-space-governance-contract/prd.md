# 空间契约修正：成员邀请放开（+ 族谱空间开辟审批方向记录）

## Goal

按产品合同修正空间邀请授权：在空间内**自己拉人、邀请人不需要审批**——active member（owner / space_admin / member）均可邀请；guest 保持最小角色仍不可邀请；受邀人仍需本人接受。族谱空间开辟的平台运营者审批流仅记录方向，另行立项。

## 背景（用户确认的产品契约，2026-08-30）

- 每个用户看到的家族空间/家族树是**按观察者计算**的（visibility per-viewer）：把人拉进空间不等于授予其超出可见性/披露规则的数据。
- 邀请本身含同意机制：invite → pending → 受邀人 resolve 接受/拒绝；因此邀请不是单方面把人纳入空间。
- 据此，此前 08-29-frontend-redesign 把邀请收紧为 owner/space_admin 属于过度收紧，本次按原设计放开到 member。
- guest 是最小可见角色，不获得邀请权。
- 家庭空间管理页（成员表、owner 移交等治理操作）仍保持 owner/space_admin（`spaceManagerOnly` 守卫不变）。

## Requirements

1. 后端 `commands/spaces.py` 的邀请授权放宽：目标空间 active membership 且 role ∈ {owner, space_admin, member} 均可通过；`guest` 返回 `403 SPACE_FORBIDDEN_ACTOR`（文案按 guest 调整）；非 active membership、无 membership、无关 platform_operator 维持 `404 SPACE_NOT_FOUND` 现状。
2. 前端 `stores/spaces.ts` 的 `canInvite` 同步：当前空间 active membership 且 role ≠ `guest`。HomeView 邀请入口对 member 显示、guest 隐藏；guest 防御提示保留。
3. `SpaceGovernancePanel` 邀请区沿用 `canInvite`；空间管理页面/入口仍仅 owner/space_admin。
4. 规范同步：`.trellis/spec/architecture.md` 授权矩阵"邀请"行更新为「active 成员（除 guest）；受邀人需接受」。

## Acceptance Criteria

- [ ] 后端：owner/space_admin/member 邀请 → 201 pending；guest → 403；无 membership 与 platform_operator → 404；pending 接受语义不变。
- [ ] 前端：member 在 Home 看到邀请入口并可发起；guest 看不到；空间管理入口/页面仍仅 owner/space_admin。
- [ ] 门禁：frontend lint/type-check/test/build；backend pytest/mypy/ruff 全绿。
- [ ] 不改动：ownership transfer、joinByUser 申请加入、guest 可见性、space-management 路由守卫、`fetchMembersByPrefix` 跨空间候选搜索语义。

## 明确不在本次范围

- 族谱空间开辟审批流（用户申请 → 平台运营者审批 → 成为新空间 owner）：另行立项。
- Agent/Steward 规划空间归属（哪些人属于哪些空间）：后续 Agent 任务。
- 空间桥接（跨空间显式连接语义）：后续设计。
