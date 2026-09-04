# 系统管理员密码认证与独立 Admin API Listener

> 前置阅读：父任务 `09-04-isolated-system-admin-platform` 的 PRD/design/notes；本任务是任务树中第一个实施的子任务。

## Goal

把系统管理员认证和家庭用户认证拆成两个完全独立的 API 面：家庭 API listener 只在 8000 服务家庭用户（名字 + PIN），系统管理员 API listener 只在 8002 以 `/admin-api` 前缀服务后台（用户名 + 强密码）。两个 listener 使用独立 JWT 签发域，任何一方的 token 在另一方都无效。家庭端从路由表、OpenAPI、认证响应和错误路径中完全无法感知系统管理员的存在。

## Requirements

### SF-F1：独立 Admin API listener

- 新增独立 `admin_app`（FastAPI），只在 8002 端口提供服务；复用 `serve.py` 既有双 listener 的生命周期、端口预检和优雅停机模式。
- `admin_app` 只注册：`/admin-api/auth/login`、`/admin-api/auth/refresh`、`/admin-api/auth/logout`、`/admin-api/auth/me`、`/admin-api/auth/password`、`/admin-api/auth/username`、`/admin-api/health`。
- 家庭 `family_app`（8000）不注册 system-admin 认证、bootstrap、admin metadata、manager applications 或旧 `admin.py` 的任何路由；对 `/admin-api/*` 及其他后台路径返回与随机未知路径一致的普通 404。

### SF-F2：管理员凭据模型

- 系统管理员认证改为用户名 + 强密码；家庭用户的名字 + PIN 完全不动。
- 单一默认管理员，初始用户名固定为 `admin`；密码不允许代码内置（禁止 `admin/admin`）。
- 数据库只保存密码哈希及版本字段：`password_hash`、`password_must_change`、`password_version`、`failed_attempts`、`locked_until`、`status`。
- 通过新的 Alembic revision 建立上述 schema 合同；不实现旧 PIN 凭据迁移，检测到无法识别的旧结构时 fail-closed。

### SF-F3：部署自动 bootstrap 与凭据文件

- 不提供任何网页初始化端点；从 family app 移除公开 `/api/bootstrap/initialize` 管理能力。
- 部署启动 preflight 在事务锁内检查：无 active system admin 时创建唯一 `admin` 账号，用 CSPRNG 生成强随机密码，原子写入数据卷 `DATA_DIR/bootstrap/admin-credentials`，权限 `0600`。
- 初始密码不进入普通应用日志、审计 detail、数据库明文或任何前端产物。
- 首次登录强制修改密码；首次成功改密事务提交后删除凭据文件；删除失败必须写安全告警且不得标记初始化完成。
- 已存在 active 管理员的部署重启不得生成第二账号。

### SF-F4：JWT 签发域与会话

- 管理员使用独立 `ADMIN_JWT_SECRET`、`ADMIN_JWT_ISSUER`、`ADMIN_JWT_AUDIENCE`；配置缺失或过弱时启动失败，不得回退默认值。
- Admin token claims：`sub`、`principal_type=system_admin`、`iss`、`aud`、`token_version`、`jti`、`iat`、`exp`。
- access token 短有效期（建议 15 分钟）；refresh token 轮换并带绝对有效期；重复使用旧 refresh 被拒绝。
- 8002 校验：签名、issuer、audience、`principal_type`、账号 active、password/token version、refresh session 状态。
- 修改用户名或密码、锁定、运维恢复都立即撤销全部 admin refresh/access 会话。

### SF-F5：账号生命周期

- 首次登录强制改密；`password_must_change=true` 时除 password/refresh/logout 外的 admin API 一律 403。
- 管理员可修改自己的用户名和密码；变更需要当前密码校验，成功后撤销全部会话。
- 忘记密码通过受限 CLI/容器运维命令恢复：生成一次性恢复密码（只落 0600 文件）、递增 `password_version`、撤销全部 refresh session、写安全审计。
- 登录失败达到阈值后锁定并返回 429 + Retry-After；错误文案统一，不泄露账号存在性。

### SF-F6：家庭端身份字段清理

- 家庭认证响应、schema 和前端类型不再包含 `system_admin`、`is_admin`、`platform_role` 等后台身份枚举。
- 家庭登录合同（名字 + PIN、错误文案、PIN 首改）保持回归不破坏。

## Out of scope

- 后台业务读模型、访问会话、审计表（子任务 2）。
- 后台前端应用（子任务 3）。
- Nginx/Compose 部署拓扑与家庭前端代码清理（子任务 4）。
- MFA、多管理员、细粒度角色。
- 旧 system_admin PIN 账号迁移或兼容登录窗口。

## Acceptance Criteria

- [ ] family token 在 8002 任意 admin 路由被拒绝；admin token 在 8000 任意家庭路由被拒绝（交叉拒绝矩阵测试）。
- [ ] 8000 的路由表和 OpenAPI 不出现 system-admin、admin-api、bootstrap 管理能力；`/admin-api/*` 在 8000 返回普通 404。
- [ ] 空库启动自动生成 `admin` 账号，随机强密码只出现在 0600 凭据文件中；日志、数据库、响应中无明文。
- [ ] 凭据文件权限为 0600；首次改密成功后文件被删除；删除失败时有安全告警且初始化未标记完成。
- [ ] 首次登录被强制改密；改密/改用户名/恢复后旧 refresh session 全部失效。
- [ ] refresh 轮换正常，重复使用旧 refresh 被拒绝，绝对有效期生效。
- [ ] 登录失败锁定返回 429，错误文案不泄露账号存在性。
- [ ] 缺失/弱 `ADMIN_JWT_*` 配置导致启动失败。
- [ ] 家庭用户名字 + PIN 登录、PIN 首改、refresh、logout 回归通过；家庭认证响应不含后台身份字段。
- [ ] 旧 `backend/app/api/admin.py` 仍未注册到任何 app。
- [ ] `ruff check`、`ruff format --check`、`mypy`、后端全量 pytest 通过。

## Notes

- 设计细节见本任务 `design.md`；实施顺序与验证命令见 `implement.md`；当前认证/listener 基线见 `research/current-auth-listener.md`。
- 依赖顺序：本任务完成后才能开始子任务 2（读模型）和子任务 3（前端）；子任务 4 最后。
