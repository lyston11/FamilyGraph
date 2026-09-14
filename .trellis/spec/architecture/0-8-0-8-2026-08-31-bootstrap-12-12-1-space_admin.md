# 0.8 系统管理员与空间唯一管理员重构（2026-08-31；⚠️ 认证/路由/bootstrap 条款已被 §12/§12.1 取代，本节仅主体分离与 space_admin 语义继续有效）

本节覆盖并替代本文早期将 `platform_operator` 绑定到家庭 `User` 的兼容描述；其系统管理员认证（PIN）、路由位置（`/api/admin/*`）、首启初始化端点与共享 JWT 域条款已废止，现行合同见 §12（独立 admin listener / 用户名+密码 / 部署自动 bootstrap / 独立签发域）与 §12.1（/admin-api/v1 只读模型与审计）：

### 1. Scope / Trigger

- 系统管理员是独立平台主体，不引用 `User`、`Account`、`FamilySpace` 或 `SpaceMember`；首启只创建 `system_admins` 与 `system_admin_accounts`。
- 家庭空间管理员是空间域关系，不是用户全局属性；规范角色只有 `space_admin`。
- 系统后台只处理账号、成员关系、管理员归属、空间元数据、申请和交接工单的最小投影。

### 2. Signatures

- `POST /api/bootstrap/initialize` → 一次性系统管理员登录名和 `one_time_pin`；数据库不得创建家庭主体。
- `GET /api/admin/accounts`、`GET /api/admin/spaces`、`GET /api/admin/space-managers`、`GET /api/admin/spaces/{space_id}/members` → 仅 `require_system_admin`，使用专用 Pydantic schema。
- `GET/POST /api/admin/manager-applications...` → 仅 `require_system_admin`；申请目标必须是已存在的 `lineage` 空间。
- `SpaceManagerApplication(applicant_user_id, space_id, status)` → 申请人必须是目标空间 active 普通成员；目标已有管理员时先创建绑定当前管理员的 `ManagerTransferConsent`。
- `is_space_manager(session, space_id, user_id)` → 只按目标空间 active `space_admin` 关系判断。

### 3. Contracts

- `SpaceMember.role` 的持久化值为 `space_admin|member`；每个正常空间最多一个 active `space_admin`，创建和交接完成后必须恰好一个。
- 旧 `owner` 只允许作为迁移/旧夹具输入，在 ORM 写入事件中归一化为 `space_admin`；不得参与授权，也不得作为 API 产品角色输出。
- JWT 必须携带 `principal_type=system_admin|family_user`；家庭端依赖拒绝 system-admin 主体，系统后台依赖拒绝普通家庭主体。
- 元数据响应不得包含档案日期、性别、简介、头像、附件、关系图边、私人会话/记忆或敏感披露字段。

### 4. Validation & Error Matrix

- 非 `lineage` 管理员申请 → `422 VALIDATION_ERROR`。
- 目标空间不存在或申请人不是 active 成员 → `404 SPACE_NOT_FOUND`（防枚举）。
- 非 member、已是目标管理员 → `403/409`，不得创建申请。
- 系统管理员未完成首登 PIN 修改 → 仅允许 PIN/登出/刷新白名单。
- 已有管理员但未取得明确同意 → `409`，不得交换角色；原管理员拒绝时保持原关系不变。
- 目标空间无管理员或并发交接校验失败 → `409`，进入修复/重新核验流程，不提交零/双管理员终态。

### 5. Good/Base/Bad Cases

- Good: 同一用户在空间 A、B 各有一条 active `space_admin`，在空间 C 仍按 C 的 member 角色授权。
- Base: 系统管理员登录后读取账号与空间元数据，但 `/api/me`、`/api/spaces` 返回认证失败，不进入家庭壳。
- Bad: 用 `is_admin(user_id)`、`owner_id` 或用户在任意其他空间的管理员身份放行当前空间治理。

### 6. Tests Required

- 首启数据库只存在独立系统主体；系统 token 可访问 `/admin/accounts`，家庭 token 返回 403。
- 系统 token 访问 `/me`、`/spaces` 被拒绝；元数据响应字段白名单无家庭档案字段。
- 路由注册断言 `/admin/accounts`、`/admin/spaces`、`/admin/space-managers` 各只有一条。
- lineage 申请、同意工单、拒绝、过期、并发交接均验证唯一 active `space_admin`。
- Alembic 空库、合法存量和 owner/space_admin 冲突数据分别验证。

### 7. Wrong vs Correct

#### Wrong

```python
if user.is_admin or family_spaces.owner_id == actor.id:
    allow_space_management()
```

#### Correct

```python
if is_space_manager(session, space_id, actor.id):
    allow_space_management()
```