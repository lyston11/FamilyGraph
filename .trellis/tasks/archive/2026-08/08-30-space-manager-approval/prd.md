# PRD：空间管理员申请审批（08-30-space-manager-approval）

## Goal（用户确认的契约，2026-08-30）

为已有家庭/族谱空间提供清晰的管理员晋升流程：

- active `member` 可以邀请其他账号加入空间，邀请不需要平台运营者审批；邀请后为 `pending` membership，受邀人本人接受后才成为 `active`。
- active `member` 可以申请成为当前已有空间的 `space_admin`，该晋升需要平台运营者审批。
- owner、已有 `space_admin` 和 guest 不能重复申请该空间管理员身份。
- 现有空间的 owner 仍只能通过 ownership transfer FSM 变更；平台运营者不能借本流程改写 `family_spaces.owner_id`。
- 用户直接创建 household/lineage 空间的既有路径保持不变；创建者成为新空间 owner + active 成员。

## 需求

### 用户侧

1. `identity_confirmed` 的当前空间 active `member` 可提交 `space_admin` 申请，必须指定已有空间。
2. 请求类型只有 `space_admin`；未知类型、额外字段和缺少 `space_id` 均返回 422。
3. 目标空间不存在、申请人不是目标空间 active member 时按 404 防枚举；owner、space_admin、guest 不适用时返回稳定错误。
4. 同一申请人对同一空间至多有一条 pending 申请；重复提交返回 `SPACE_MANAGER_APPLICATION_EXISTS`。
5. 申请人可查看自己的申请状态：`pending` / `approved` / `rejected`，以及平台备注。
6. member 邀请能力不由本流程收紧；active member（除 guest）仍可直接发起邀请。

### 平台运营者侧（`/admin`）

7. 仅 `platform_operator` 可读取管理员申请队列和执行裁决。
8. 队列只展示申请人、申请类型、目标空间名、时间、状态，不展示家庭档案敏感字段。
9. `approve`：在同一事务中将目标空间 active `member` 升级为 `space_admin`；不改变 owner。
10. `reject`：必须填写非空理由；申请进入终态并保留备注。
11. 已裁决申请再次裁决返回 `SPACE_MANAGER_APPLICATION_DECIDED`；审批期间申请人资格变化则返回 409 且申请回到 pending。
12. 提交、批准、驳回都写审计；裁决写入 `space.manager_application.decided` 领域事件。

## 前端

13. Home 在当前空间且角色为 member 时展示“申请成为管理员”，弹窗明确目标空间与平台审批；guest 不展示。
14. Home 保留“创建家庭空间”自由创建入口；成员邀请入口对 owner、space_admin、member 保持可用，对 guest 隐藏。
15. MemberCreateWizard 保留“新建族谱空间”直建能力，创建后将新空间加入空间列表并选中。
16. AdminView 的平台运营队列仅展示“申请成为空间管理员”，提供通过和带必填理由的驳回。
17. 文案区分“平台运营后台”“家庭空间管理”“申请成为管理员”，不把平台角色和空间角色混称。

## Acceptance Criteria

- [ ] active member 可提交已有空间 `space_admin` 申请；owner、space_admin、guest、非 active 成员、未确档用户按合同拒绝。
- [ ] 只接受 `space_admin` 和必填 `space_id`，不接受其他申请类型或空间拟议名称字段。
- [ ] 重复 pending 稳定 409，驳回后可重新提交。
- [ ] 仅 platform_operator 可查看/裁决；approve 只升级 member，不改变 owner；reject 理由必填。
- [ ] 并发重复提交只有一个成功；并发裁决只有一个成功，另一个稳定 409；审批副作用失败时申请仍为 pending。
- [ ] `POST /api/spaces` 自由创建 household/lineage 的原有路径回归通过；邀请仍无需平台审批。
- [ ] 前端 Home/Admin/Wizard 交互和类型检查通过。
- [ ] backend pytest/mypy/ruff、frontend lint/type-check/test/build 全绿（并行 runtime 文件的独立问题另行记录）。
- [ ] 0021 迁移只在 0020 已落地后按父链顺序发布。

## 明确不在本次范围

- 新空间开辟审批或关闭自由建空间；
- 邀请审批、空间桥接实体、Agent/Steward 自动归属规划；
- owner transfer FSM 本身的改动；
- 申请结果通知和角色降级/撤销接口。
