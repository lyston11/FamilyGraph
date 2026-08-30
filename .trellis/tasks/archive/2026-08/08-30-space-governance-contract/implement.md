# Implement 记录：空间邀请放开到 active member（除 guest）

日期：2026-08-30。依据 prd.md（用户确认产品契约 2026-08-30）：visibility 按观察者计算、
邀请自带同意机制（invite → pending → 受邀人接受），因此空间内自己拉人不需要审批；
guest 为最小可见角色不获得邀请权。族谱空间开辟审批方向仅记录，另行立项。

## 改动

### 后端

- `backend/app/commands/spaces.py`
  - `_require_space_manager` → `_require_inviter`：`_require_active_member` 之后仅当
    `member.role == "guest"` 时 raise `403 SPACE_FORBIDDEN_ACTOR`（文案「访客不能邀请成员」）；
    owner / space_admin / member 均放行。`invite_member` 调用点同步。
  - 非 active membership / 无 membership / 无关 platform_operator 仍由
    `_require_active_member` → `404 SPACE_NOT_FOUND`（现状保持）。
- `backend/tests/test_space_invite_authz.py`
  - 正向参数化扩为 `["owner", "space_admin", "member"]` → 201 pending；
  - guest 单独负向：403 `SPACE_FORBIDDEN_ACTOR` 且不创建 membership 行；
  - platform_operator 无 membership → 404 `SPACE_NOT_FOUND` 保持。

### 前端

- `frontend/src/stores/spaces.ts`：`canInvite` 改为当前空间 active membership 且
  `currentRole !== 'guest'`（currentRole 本就只从 active membership 派生；
  `canManageSpace` 保持 owner/space_admin 不变）。
- `frontend/src/views/HomeView.vue`：邀请入口继续用 `canInvite`（member 现在显示）；
  两处防御 message 更新为「访客不能邀请成员」。
- `frontend/src/components/member/SpaceGovernancePanel.vue`：无需改动——邀请区本就以
  `spaces.canInvite` 门控，自动放开；移交区仍 `canTransferOwnership`（仅 owner）。

### 测试同步

- `frontend/src/views/__tests__/home.spec.ts`：「邀请入口对 active 成员
  （owner/space_admin/member）显示」+「guest 不显示邀请入口」。
- `frontend/src/stores/__tests__/spaces.spec.ts`：新增 canInvite 用例——
  owner/space_admin/member=true、guest=false、无 active membership=false。
- `frontend/src/components/member/__tests__/SpaceGovernancePanel.spec.ts`：
  member 可见邀请区但无移交区；guest 两者均不可见。
- 核对其余引用点：`SpaceGovernanceDialog.spec.ts`（owner/space_admin 可邀请、guest 不可）、
  `space-management.spec.ts`（member/guest 进入管理页仍 denied，守卫未动）、
  `guard.spec.ts` / `family-space.spec.ts`（仅 API mock）——均与新契约一致，无需改动。

### 规范

- `.trellis/spec/architecture.md` §6 授权矩阵：原矩阵无「邀请」行，新增一行
  「空间邀请（invite）｜—｜active 成员（除 guest）可邀请；受邀人需接受｜—｜—｜—」，
  不动矩阵其他行与文件其他章节。

## 明确未动（约束遵守）

- ownership transfer、joinByUser 申请加入、guest 可见性、space-management 路由守卫
  （`spaceManagerOnly` → `canManageSpace`）、`fetchMembersByPrefix` 跨空间候选搜索语义。
- 并行 v2-agent-runtime 未提交改动（agent/*、backend agent 相关文件、docker-compose.yml、
  README.md 等）一律未触碰。

## 验证结果

- 后端（backend/）：
  - `.venv/bin/python -m pytest -q` → 571 passed, 1 failed。
    唯一失败 `tests/test_internal_agent_api.py::test_context_returns_complete_transcript_
    without_recent_limit` 为并行 v2-agent-runtime 工作树内既有问题（该文件有 69 行未提交
    改动，NameError: name 'provider' is not defined @ L272），与本次改动无关，未触碰。
  - `.venv/bin/python -m mypy app` → Success: no issues found in 120 source files。
  - `.venv/bin/python -m ruff check app/commands/spaces.py tests/test_space_invite_authz.py`
    → All checks passed；`ruff format --check` 同样通过。（全仓 ruff 的 7 处报错与 1 个
    格式问题均集中在并行任务的 `tests/test_internal_agent_api.py`，非本次范围。）
- 前端（frontend/）：
  - `npm run lint` → 通过（无输出）。
  - `npm run type-check` → 通过（无输出）。
  - `npm run test` → Test Files 40 passed (40)，Tests 243 passed (243)。

## 验收对照

- [x] 后端：owner/space_admin/member 邀请 → 201 pending；guest → 403；
      无 membership 与 platform_operator → 404；pending 接受语义未改。
- [x] 前端：member 在 Home 看到邀请入口并可发起；guest 看不到；
      空间管理入口/页面仍仅 owner/space_admin。
- [x] 门禁：frontend lint/type-check/test、backend pytest/mypy/ruff 全绿
      （唯一 pytest 失败与 ruff 报错均为并行任务既有问题，文件未触碰、已定位说明）。
- [x] 不改动清单逐项确认。
