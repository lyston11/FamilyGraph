# 0.10 家庭端自助注册与邀请码（2026-09-05，任务 09-05-family-account-provisioning）

### 1. Scope / Trigger
- 家庭端（8000）新增公开注册端点、邀请码域（三类型）与并流绑定域（account_bindings）；新增迁移 0030/0031。**8002 admin listener 零新增路由、system-admin-frontend 零痕迹**（延续 §12 拓扑）。
- 身份三条状态机**不新增转换点**：注册是初始态直接落库（非 managed→claimed 转换）；`identity_confirmed` 仅经 `identity_fsm.confirm_profile_identity` 既有入口（绑定确认是唯一新增调用点）。
- §0.6 owner onboarding link 兑换端点保留不变；其 admin-web 签发入口留作后续选项（陌生人码已覆盖新家庭进入）。

### 2. Signatures
- `POST /api/auth/register`（公开）：`{username, pin, display_name(接收不持久化，单名模型并入 users.name), invite_code?}` → token pair。命令层 `commands/registration.py::register_user`（单立即事务）。
- `GET|POST /api/invite-codes`、`DELETE /api/invite-codes/{id}`、`POST /api/me/invite-codes/redeem`；码原语 `services/invite_codes.py`（生成去 0/O/1/I/L、resolve 字段级文案、`consume_code` 条件 UPDATE 原子核销）。
- `GET /api/bindings`、`POST /api/bindings/{id}/confirm|reject`、`DELETE /api/bindings/{id}`；命令层 `commands/bindings.py`。
- DB：`invite_codes(code UNIQUE, kind∈{household,lineage,stranger}, creator_id nullable SET NULL（迁移 0032，原 RESTRICT 会永久挡住创建者删除——产品无删码入口，已撤销码同样触发 RESTRICT）, space_id nullable, max_uses, used_count, expires_at, revoked_at)`，CHECK：`stranger ⇔ space_id IS NULL`、household/lineage ⇒ `space_id NOT NULL AND max_uses IS NOT NULL AND max_uses=1`（SQLite CHECK 对 NULL 放行，必须显式排除）。`account_bindings(initiator_id CASCADE, target_id CASCADE, person_id SET NULL, status∈{pending,confirmed,rejected,cancelled})`。
- 配置：`REGISTRATION_ENABLED`（默认 True）经 `GET /api/bootstrap/status` 加法字段 `registration_enabled` 投影。

### 3. Contracts
- 注册初始态：`Account(status='claimed', pin_must_change=False)`（自选 PIN 无需强制改）+ `User(profile_status='provisional')`。能力边界（决策 13 修订 2026-09-05，用户拍板撤销 provisional 建码限制）：**每个已登录账号都可建码与邀请**——household/lineage 码须为该空间 active 成员，stranger 码任何账号可建（纯归因）；接受码无身份门槛；身份确认（identity_confirmed）影响的是推荐资格与空间管理员申请等能力，不影响码域。
- 码语义：household/lineage 码一次性，加入**只走 SpaceMember FSM**（`space_fsm.invite`(pending) → `transition("accept")`，`added_by`=码创建者，审计 `space_invite_accepted`+`invite_code_redeemed`）；stranger 码多人次可设上限，仅归因（audit `invite_code_redeemed`，scene=register）——持码注册者经 `create_space(commit=False)` 得自己独立 household 空间（创建者即 space_admin，符合 §0.8 唯一管理员不变量），与码创建者**零** SpaceMember/可见性关系。设置页兑换与注册时填码同一 `join_space_with_code` 路径（scene=redeem）。
- 并流绑定：建档撞名判定作用域 = 建档既有查重门禁口径（非全局用户名匹配，§0.9 同名不同人必须放行）；目标资格 = `Account.claimed` 且 `created_by IS NULL`。确认 = 本人 + PIN 复验（登录侧 auth_guard 同源失败计数）+ `confirm_profile_identity`，人物并回仅迁身份承载行（refs/members/边/附件/事实），不覆写存储值；拒绝/取消即删除撞名人物。
- 删除联动：删除用户时 `delete_profile_core` 调用 `auto_revoke_for_creator_delete` 自动撤销其全部未撤销码（audit `invite_code_auto_revoked_on_delete`，自删场景 actor_id=None）；码行经 0032 `creator_id SET NULL` 保留使用计数与撤销历史，归因由 audit_log 快照承载。

### 4. Validation & Error Matrix
- 开关关 → 404 与随机未知路径逐字节一致（body 校验前短路）；前端 `/register` 同形 404（URL 保留）
- 注册限流 → 429 + Retry-After（进程内滑窗）；用户名占用 → 409 防枚举统一文案 + dummy-bcrypt 时序对齐
- 码无效/过期/撤销/用尽 → 400 字段级文案（不含创建者信息）；stranger 码在设置页兑换 → 400 且不核销
- provisional 建码 → 403；已是空间成员再兑码 → 409 且**不核销**码
- 绑定：非本人与不存在同形 404；重复确认 409；confirmed/rejected 终态不可逆

### 5. Good/Base/Bad Cases
- Good：零账号库注册首号 → 自建空间正常使用（冷启动闭环）；家人持家庭码注册即 active 成员。
- Base：无码注册 → §215 空态引导建「我的家庭」。
- Bad：任何绕过 SpaceMember FSM 直接写 active 成员；注册即 identity_confirmed；码文案泄露创建者/空间信息；8002 出现注册/码路由。

### 6. Tests Required
- `tests/test_register_api.py`（冷启动/四分支/开关 404/限流/防枚举/初始态断言）、`tests/test_invite_codes_service.py` + `test_invite_codes_api.py`（约束/核销竞态/撤销权限/provisional 403）、`tests/test_bindings.py`（撞名转绑定/确认并回无残留/终态）、`tests/test_bootstrap_api.py`（registration_enabled 投影）、`test_members_api.py`（bound_to_existing 白名单）；前端 `register.spec.ts`、`InviteCodeSection.spec.ts`、`guard.spec.ts`。

### 7. Wrong vs Correct
#### Wrong
```python
SpaceMember(space_id=..., user_id=..., role="member", status="active")  # 持码加入直接造 active
```
#### Correct
```python
space_fsm.invite(...)                      # pending，added_by=码创建者
space_fsm.transition("accept", ...)        # 唯一路径转 active，审计同形
```