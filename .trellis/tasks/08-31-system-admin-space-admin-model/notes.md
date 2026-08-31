# 规划注记：系统管理员与空间唯一管理员模型

## 用户确认的不可变产品规则（2026-08-31）

- 一个家族/家庭空间只有一个属于该空间本身的管理员。
- 一个用户可以同时成为多个不同空间的管理员；管理员资格始终按目标 `space_id` 绑定。
- 空间成员列表中可以有多个“管理员”用户，但他们可能分别管理别的空间；别的空间管理员身份不改变其在当前空间的角色和权限。
- 示例：父亲是当前家庭空间管理员；母亲是母系家族空间管理员。母亲在当前家庭空间仍是普通成员。
- 系统管理员是独立的系统后台主体，没有自己的用户、家族、家庭空间或家庭资料。
- 系统管理员只查看账号、成员关系和空间元数据。
- `owner` 与 `space_admin` 是同一个产品层“空间管理员”概念，不应作为两个用户可见的管理员等级。

## 当前代码证据与偏差

- 当前 bootstrap `backend/app/services/bootstrap.py` 创建 `User + Account + PlatformRoleAssignment`，因此平台管理员仍被建模为家庭 `User`。
- 当前认证 `backend/app/api/auth.py` 和 `backend/app/api/deps.py` 以 `User + Account` 作为唯一主体，JWT `sub` 是 user id。
- 当前平台角色位于 `platform_role_assignments`，`platform_operator` 已被 visibility 排除家庭数据读取，但尚未成为独立认证主体。
- 当前 `FamilySpace` 同时有 `owner_id`，`SpaceMember.role` 同时允许 `owner` 和 `space_admin`；`manager_applications` 将申请人从 member 升为 `space_admin`，而 ownership transfer 将原 owner 降为 `space_admin`。这会与“每空间唯一管理员”冲突，必须在设计/实现中统一。
- 当前前端 `AppShell.vue` 同时承载家庭导航和平台运营后台入口，`AdminView.vue` 挂在 `/admin`；需要独立系统后台 shell 与主体路由。
- 当前前端和后端仍保留 `is_admin` 兼容投影；该字段不能继续作为新权限模型的长期来源。

## 关键设计警告

- 不要将“用户是否是任何空间管理员”存成全局布尔值；必须查询具体 `(user_id, space_id)`。
- 不要因为用户在空间 B 是管理员，就允许其管理空间 A。
- 不要通过随机选择、删除关系或静默降级解决迁移中的多管理员冲突。
- 不要让系统管理员为了登录后台而创建虚假的家庭档案或空间成员关系。
- 不要让系统后台查询复用会带出家庭档案内容的普通成员/图谱 API。
- 不要把 `owner` 移交和“申请成为空间管理员”合并成一个没有唯一性保护的流程。

## 新增用户确认：管理员申请必须经过原管理员同意（2026-08-31）

- 当目标 `lineage` 家族空间已有本空间管理员时，其他符合资格的成员仍可提交申请；“已有管理员”不是提交禁用条件。
- 系统管理员审核后必须向目标空间当前管理员发送可追踪的工单或通知；原管理员明确同意前，系统管理员不得直接完成管理员交换。
- 原管理员拒绝时保持现有管理员关系不变；原管理员同意后，系统管理员才可在同一事务中将申请人升为唯一 `space_admin`、将原管理员降为普通 `member`，并记录完整审计。
- 工单必须绑定申请、目标空间和发送时的当前管理员；管理员身份发生变化或工单过期后，旧同意不可复用。
- 用户侧申请卡片必须明确写出目标家族空间名称和类型，不能显示无目标的“申请成为管理员”；服务端以 `space_id` 解析并返回目标名称。
- 通知实现至少需要可审计的站内通知/待办；短信、邮件和推送渠道不属于本任务。

## 已知兼容与范围

- 现有空间管理员申请任务 `08-30-space-manager-approval` 的旧测试仍以 household 目标、owner 角色和直接升级为前提；这些断言与本任务锁定的 lineage 目标、唯一 `space_admin` 和原管理员同意工单冲突，必须迁移测试契约，不能通过恢复旧授权语义解决。
- `platform_operator` 仅作为迁移输入标识；运行时首启创建独立 `SystemAdmin`/`SystemAdminAccount`，家庭用户 token 不具备系统后台权限。
- 旧 `/admin` 前端路径仅重定向到 `/system-admin`；后台账号、空间、成员和交接工单查询由专用 schema 的 `admin_metadata` 路由提供，申请队列/裁决由 `system_admin` 路由提供。
- 现有未提交修改 `backend/tests/conftest.py` 已保留；迁移 `0022_system_admin_space_manager` 在 SQLite 空库及测试迁移链执行通过。

## 实施核验记录（2026-08-31）

- 后端新增独立主体模型、认证/refresh/PIN 路径和系统后台最小元数据投影；`/me`、`/spaces` 等家庭端点使用 `require_authenticated_user`，系统主体访问被拒绝。
- `SpaceMember` 的生产规范角色为 `space_admin|member|guest`，旧 ORM `owner` 输入只在写入事件中归一化为 `space_admin`；新建空间和 owner invitation 均写入 `space_admin`。
- 已修复系统后台路由重复注册：`/admin/accounts`、`/admin/spaces`、`/admin/space-managers` 每个只保留一条专用元数据路由。
- 新增 `backend/tests/test_system_admin_boundary.py`，验证系统管理员无家庭主体、可访问最小后台元数据、不能访问家庭端点，普通家庭用户不能访问后台。
- 已验证：后端 ruff、format、mypy、Alembic head、系统管理员边界测试；前端 type-check、lint、build。全量旧套件仍有旧产品契约断言：后端 26 个、前端 4 个，集中在旧 `owner`/家庭 `is_admin`/household 申请/旧 `/admin/users` 与旧平台运维员夹具；这些测试需要随契约迁移，不能通过放宽生产权限修复。

## 契约迁移与端到端闭合（2026-08-31 第二轮）

前一轮遗留的 26 个后端 / 4 个前端旧契约断言已全部迁移，并补齐了申请到交接的完整链路。

- 迁移 `0023_system_admin_decision_ref`：`space_manager_applications` 增加 `system_admin_decided_by`（FK → `system_admins`，`SET NULL`）。裁决人身份必须独立记录，因为系统管理员不是家庭 `User`，无法写进 `decided_by`；两列互斥。
- 授权不再有 `owner_id` 兜底：`internal_agent.py` 的 fallback 已删除，运行时只看目标空间的 active membership。原先依赖它的 16 个 agent/proxy 测试是夹具缺 `SpaceMember` 行，已在夹具侧补齐，生产代码保持无兜底。
- 前端角色收敛为 `space_admin | member | guest`，`isSpaceOwner` 已删除，`canManageSpace`/`canTransferOwnership` 都等于 `isSpaceAdmin`。「所有者 / 所有权移交」文案统一为「空间管理员 / 管理员交接」，并在发起处明说「对方接任、你降为普通成员」。
- 申请入口改为显式目标卡片：`GET /spaces/manager-applications/eligible-targets` 返回候选 lineage 空间、目标名称、现任管理员和 `has_pending_application`。前端不再按本地 `currentRole === 'member'` 推断资格，服务端返回空候选时入口直接不出现（fail-closed）。
- 原管理员工单在首页闭环：待处理工单列出目标空间与申请人、写明交接后果，同意/谢绝经 `POST /spaces/manager-transfer-consents/{id}/decision`，谢绝理由前端先拦一次、后端仍兜底。
- `SystemAdminView` 从只读表格改为可操作后台：申请队列（含两阶段状态提示）、工单全量视图、按需展开的单空间成员元数据、受理/驳回。驳回理由为空时不发请求。
- 系统管理员硬刷新不再被踢出：`auth.ts` 的 resume 对 `principal_type === 'system_admin'` 和 `pin_must_change` 跳过家庭 `GET /me`，身份投影取 refresh 响应（服务端按签名 token 查表签发）。

顺带修掉三个不在原始清单里的缺陷：

- `respond_to_transfer_consent` 的失效写入原先与 409 同事务，`raise` 会把 `status = "expired"` 一起回滚，工单永远停在 pending 且可反复重试。现在失效判定独立成一个先提交的事务。
- `decide_manager_application_as_system_admin` 缺少 `space.manager_application.decided` 领域事件，与家庭裁决路径不对等，已补齐（不带 `actor_account_id`，裁决人用 `system_admin_id` 表达）。
- 工作树里 `_require_inviter` 被收窄成仅管理员可邀请，与 architecture.md §0.7 权限矩阵冲突且超出本任务范围，已回退为「active 成员（除 guest）可邀请」。

验证：后端 591 passed / 3 skipped，ruff、ruff format、mypy（125 files）、Alembic head `0023_system_admin_decision_ref` 全绿；前端 258 passed（41 files）、type-check、lint、build 全绿。

## 阻塞项：`admin.py` 路由未注册

`app/main.py` 没有 `include_router(admin_router)`，且 `admin.py` 的路由仍要求家庭 `User` + `platform_operator`——而迁移 0022 正好清除了这个身份。因此以下 HTTP 入口当前不可达：owner 邀请签发/吊销、数据权利更正决议、认领争议决议、审计日志查询。

本轮的处理方式是不动这三张治理表的语义：owner-invitation 覆盖改走命令层（`onboarding.create_owner_invitation` / `revoke_owner_invitation`），确实被本任务取代的断言做了迁移，PRD 明确列为 out of scope 的 break-glass 家庭数据访问用 `@pytest.mark.skip(reason=BREAK_GLASS_PENDING)` 标出而非删除。

要恢复这些入口，每张治理表需要：一列可空的系统管理员 FK（与 `system_admin_decided_by` 同构），加一个走 `require_system_admin` 的命令变体。这属于独立任务，因为它涉及「系统管理员能否触碰家庭数据」这个 PRD 已划为 out of scope 的判断。

## 已延后：guest 概念

用户指出家庭图谱不应该有访客概念。本轮按用户要求先完成当前任务，未动 guest。

guest 不是独立产品决策，它是 commit `c79eee9`（v2 四级可见性合同）里被 *拒绝* `household_detail` 的那个角色顺带引入的。今天它没有生产写入路径（所有写入都是 `space_admin` 或 `member`，`SpaceInviteCreate` 没有 role 字段），但仍被 `visibility.py:200`、`controlled_web.py:62`、`services/controlled_web.py:326`、`steward.py:970` 读取，并写死在 `architecture.md:90` 与 `database-guidelines.md:17`。

因此移除 guest 要先定范围：只清产品表面（前端角色映射 + 文案），还是连数据模型与可见性策略一起改（需要同时改上述两份 spec）。`manager_applications.py:53` 的 `_reject_guest_only` 无论选哪条都是死代码。

## 预期实施后必须回答的问题

- 系统管理员主体是否真正脱离 `User`，或过渡方案如何阻止其进入家庭域？
  独立主体：`SystemAdmin` / `SystemAdminAccount` 两张表，JWT 带 `principal_type` 声明。家庭端点统一用 `require_authenticated_user`，系统主体访问返回 401/403；后台端点用 `require_system_admin`，家庭 token（包括带旧 `is_admin` 兼容投影的账号）访问返回 403。首启 bootstrap 只创建系统主体，不创建任何 `User`/`Account`。
- `owner_id` 与唯一 manager 的关系是什么？owner transfer 完成后原 owner 的产品角色是什么？
  `owner_id` 只是历史创建者记录，不参与任何运行时授权判定。授权唯一来源是 `space_members` 里该空间的 active 行。交接完成后受让人成为唯一 `space_admin`，原管理员降为 `member`——不是「两个管理员」，由 `space_id WHERE role='space_admin' AND status='active'` 的部分唯一索引强制。
- 旧 `/admin/users` 如何安全迁移为账号/元数据接口？
  拆成 `admin_metadata` 路由下的最小元数据投影：`/admin/accounts` 只给 account_id / subject_id / subject_type / status / locked_until，不含姓名等家庭 PII；空间成员构成按 `space_id` 单独查询且只返回姓名与角色/状态。未知 `space_id` 返回空数组而不是 404，避免把端点变成家庭数据存在性探针。旧 `/admin/users`（含 break-glass 的按名字反查）未迁移，随上面的阻塞项一起归入独立任务。
- 每空间唯一管理员约束如何在 SQLite/Alembic 和并发命令层同时保证？
  两层：迁移 0022 建部分唯一索引（`batch_alter_table`，SQLite 可用），命令层在同一个 `command_transaction` 里完成 authorize → FSM → 换人写入，冲突方拿到 IntegrityError 而不是静默覆盖。并发用例覆盖了重复提交只有一个赢家。
