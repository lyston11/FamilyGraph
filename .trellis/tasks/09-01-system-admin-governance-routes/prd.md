# 系统管理员治理路由与最小权限边界

## Goal

在既有独立 SystemAdmin 主体、JWT `principal_type`、最小治理 API 和主体互斥路由的基础上，完成真实系统管理员登录、PIN 首改、刷新、登出、会话失效回跳和前端后台壳收口；保持旧 `admin.py` 未注册，不重新开放家庭数据 break-glass。

## Background / Confirmed Facts

- `backend/app/models/system_admin.py` 已有独立 `SystemAdmin`、`SystemAdminAccount`、`SystemAdminRefreshSession`，不引用家庭 `User`、`Account`、`FamilySpace` 或 `SpaceMember`。
- `backend/app/api/auth.py` 已签发带 `principal_type=system_admin|family_user` 的 JWT，并使用独立 system-admin refresh session。
- `backend/app/api/deps.py` 已有 `require_system_admin` 和 `require_authenticated_user`；家庭端点拒绝 system_admin，后台端点拒绝普通家庭主体。
- `backend/app/api/admin_metadata.py` 和 `backend/app/api/system_admin.py` 已提供账号、空间、管理员归属、成员元数据、申请和交接工单的最小治理 API。
- `backend/app/main.py` 已注册安全的 system-admin/admin-metadata routers，但没有注册历史 `backend/app/api/admin.py`。
- `frontend/src/router/index.ts` 已有 `/system-admin` 和公开 `/system-admin/login`，守卫已有主体互斥；`SystemAdminLoginView.vue` 仍是占位页。
- `frontend/src/views/ChangePinView.vue` 当前改 PIN 后无条件跳家庭 `/login`；`frontend/src/stores/auth.ts` 当前会话失效也固定跳家庭 `/login`。
- `SystemAdminView.vue` 当前存在与 `09-01-remove-guest-role` 相关的未提交 guest 删除修改，实施时必须保留。
- 历史 `admin.py` 包含家庭 PIN 重置、资料修改、custody transfer、claim dispute、data rights 等 break-glass，不能因本任务而重新挂载。

## Requirements

### SAR-F1：真实系统管理员登录页

- 将 `/system-admin/login` 占位页改为真实表单，复用已有 `/api/auth/login` 认证协议，不新增第二套凭据。
- 登录成功后必须硬校验返回 `principal_type === "system_admin"`；family_user 凭据不能建立 system-admin 会话。
- 错误文案统一且不泄露账号是否存在；登录、PIN 必改和 redirect 状态符合现有认证安全合同。
- 已登录 system_admin 访问登录页应进入后台或首改 PIN；已登录 family_user 不能进入后台。

### SAR-F2：PIN 首改、刷新和主体会话

- 复用 `/api/users/me/pin` 的 system-admin 分流和 token_version 失效机制。
- system_admin 首登改 PIN 后回到 system-admin 登录入口或后台，不得进入家庭 `/login`。
- system_admin refresh 始终返回 system_admin，不得转换为 family_user；family_user refresh 行为保持不变。
- system_admin 会话失效跳 `/system-admin/login`；family_user 会话失效仍跳 `/login`。
- 登出撤销正确主体的 refresh/session 状态并清理系统管理员缓存。

### SAR-F3：后台壳与最小治理 API

- `SystemAdminShell` 增加登出入口，不渲染家庭导航、关系图、Memory、Session 或家庭档案。
- 所有治理 API 保持 `require_system_admin`，并覆盖无 token、family_user、错误 principal_type 的拒绝测试。
- schema 只允许账号元数据、成员关系元数据、空间元数据、申请和交接工单必要字段；未知 space_id 的查询不得形成存在性探针。
- 保持旧 `admin.py` 未注册，并把家庭 break-glass 能力明确留到独立任务。

## Acceptance Criteria

- [ ] `/system-admin/login` 不再是占位页，可以提交登录并正确处理 system_admin/family_user 两种主体。
- [ ] family_user 凭据不会建立 system-admin 会话或进入后台。
- [ ] system_admin 首登改 PIN 后不会被送到家庭 `/login`，旧 token 按 token_version 失效。
- [ ] 两种主体的 refresh、logout 和 session expired 导航均正确隔离。
- [ ] SystemAdminShell 可登出且不包含家庭壳或家庭数据入口。
- [ ] 治理 API principal、权限、最小字段和未知空间防枚举测试通过。
- [ ] `backend/app/api/admin.py` 保持未注册；没有新增家庭 PIN 重置、档案修改或其他 break-glass 能力。
- [ ] 后端定向测试、Ruff、mypy，以及前端 type-check、lint、test、build 通过。
- [ ] 当前 `SystemAdminView.vue` 的 guest 删除 WIP 未被覆盖或回退。

## Constraints

- 必须遵守 `.trellis/spec/architecture.md` §0.8：system_admin 是独立平台主体，不能进入家庭可见性链；空间治理只按目标空间 active `space_admin` 判断。
- 不使用 `users.is_admin`、`owner_id` 或其他空间的管理员身份作为后台授权。
- 不注册旧 `admin.py`，不把未迁移的家庭 break-glass 路由包装成安全治理 API。
- 保留当前工作树所有 unrelated WIP。

## Out of Scope

Provider 管理、Agent runtime 运维、家庭档案直接浏览、旧 `/admin/users`、任意家庭 PIN 重置、家庭资料修改、custody transfer、claim dispute、data-rights break-glass、多级系统管理员。
