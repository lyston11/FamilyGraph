# 12. 独立系统管理员平台与三 Listener 拓扑（2026-09-04，取代 §0.8 的认证/路由/bootstrap 条款）

§0.8 中"系统管理员与空间唯一管理员"的主体分离与 `space_admin` 语义继续有效；本节取代其认证协议（PIN → 用户名+密码）、路由位置（`/api/admin/*` → `/admin-api/*`）、首启方式（公开 initialize 端点 → 部署自动 bootstrap）与 JWT 签发域（共享 → 独立）条款。

### 1. Scope / Trigger

- 新增任何后台 API、管理员认证、跨 listener 路由调整时适用本节。
- 三个 FastAPI app 同进程：family `app`（8000，仅家庭认证+业务）、internal `internal_app`（8001，Agent）、admin `admin_app`（8002，仅系统管理员）。共享 engine/lifespan，不共享 router 对象。
- 家庭端产品边界：家庭 bundle、路由表、OpenAPI、认证响应不出现任何后台痕迹；`/admin-api/*` 在 8000 必须与随机未知路径逐字节一致的普通 404（存在性 oracle 红线）。

### 2. Signatures

- admin 路由面（仅 8002）：`POST /admin-api/auth/login`（username+password）、`POST /admin-api/auth/refresh`（轮换）、`POST /admin-api/auth/logout`、`GET /admin-api/auth/me`、`PUT /admin-api/auth/password`、`PUT /admin-api/auth/username`、`GET /admin-api/health`。
- 模型：`system_admins(username 唯一, status)`；`system_admin_accounts(password_hash, password_must_change, password_version, failed_attempts, locked_until, status, claimed_at)`；`system_admin_refresh_sessions(token_hash, rotated_from_id, expires_at, revoked_at, last_seen_at)`。迁移 `0028_admin_password_credentials`；存量 PIN 数据 fail-closed（RuntimeError 拒启），永不静默转换。
- 恢复 CLI：`python -m app.admin_recovery [--username admin]`。

### 3. Contracts

- 独立签发域（强制）：`ADMIN_JWT_SECRET`（≥32 且 ≠ 家庭 `SECRET_KEY`）、`ADMIN_JWT_ISSUER`、`ADMIN_JWT_AUDIENCE`（必填且互不相等）；缺失/过弱启动失败，无开发逃逸开关。access TTL 900s（`ADMIN_ACCESS_TOKEN_TTL_SECONDS`），refresh 绝对有效期（`ADMIN_REFRESH_TOKEN_TTL_SECONDS`，轮换不续期）。admin claims：`sub/principal_type=system_admin/iss/aud/token_version/jti/iat/exp/typ`。
- 交叉拒绝：admin token 在 8000 任意家庭路由 401；family token 在 8002 任意 admin 路由 401。不能只靠 `principal_type` 区分——iss/aud/secret 必须物理隔离。
- 会话撤销触发器：修改用户名、修改密码、锁定、运维恢复都必须 `password_version+1` + 撤销全部 refresh session（锁定分支也不例外）。
- bootstrap 凭据文件：`DATA_DIR/bootstrap/admin-credentials`，`mkstemp(0600)+os.replace` 原子写；初始用户名固定 `admin`，密码 CSPRNG，只存哈希；首次改密事务提交后删除，删除失败写安全告警 + `admin_credential_file_delete_failed` 审计 + 回置 `password_must_change=true`；已有 active/disabled 管理员时重启不生成第二账号。
- `AdminSessionOut` 白名单精确集合：`id/username/password_must_change/status`；家庭 `UserOut` 不含 `is_admin/platform_role`，`principal_type` 收窄为 `Literal["family_user"]`。

### 4. Validation & Error Matrix

- 用户名或密码错误 / 账号不存在 / 主体错误 → 统一 `401 ADMIN_INVALID_CREDENTIALS`（文案"用户名或密码错误"，防枚举，dummy 哈希对齐时序）。
- 失败达阈值 → `429` + `Retry-After`，同时撤销全部会话。
- `password_must_change=true` → 仅 password/refresh/logout 可用，其余 admin 路由 403。
- refresh 重用 → 401 并撤销该主体全部会话；refresh 过期（即使 JWT 未到真实时钟）→ 401 不签发新会话。
- 家庭 listener 上任何 admin 路径 → 普通 404（不是 403/重定向/自定义错误页）。

### 5. Good/Base/Bad Cases

- Good: 空库启动生成唯一 `admin` + 0600 文件；首登改密后文件消失、旧会话全部失效；把 admin token 复制到 8000 被拒。
- Base: 已初始化部署重启不生成第二账号；8002 独立健康检查 `/admin-api/health`。
- Bad: `admin/admin` 内置密码、把 PIN 当密码、admin router 挂回 8000、共享 issuer/audience 只靠 principal_type 放行、密码进日志/DB 明文/审计正文。

### 6. Tests Required

- 路由注册断言：8000 无 admin 业务路由；8002 恰好七条 admin 路由；旧 `admin.py` 全部 break-glass 路径在两个 listener 均不存在。
- 交叉拒绝矩阵（双向）；`/admin-api/*` 在 8000 与随机未知路径逐字节一致。
- bootstrap：唯一账号、文件 0600、日志 grep 无明文、改密后删除、删除失败回置 must_change、lifespan 接线空库启动。
- refresh：轮换后 `expires_at` 不续期（绝对有效期）、重用拒绝并撤销全部会话、行到期 401。
- 密码生命周期：首登强制、改密/改用户名/锁定/恢复后版本+1 且会话全撤销。

### 7. Wrong vs Correct

#### Wrong

```python
# 共享签发域，仅靠 principal_type 区分——复制 token 即越界
token = issue_jwt(sub=admin.id, principal_type="system_admin", secret=SECRET_KEY)
app.include_router(system_admin_router, prefix="/api")  # 后台路由挂上家庭 listener
```

#### Correct

```python
token = issue_admin_jwt(sub=admin.id, iss=ADMIN_JWT_ISSUER, aud=ADMIN_JWT_AUDIENCE,
                        secret=ADMIN_JWT_SECRET)  # 独立签发域
admin_app.include_router(admin_auth_router, prefix="/admin-api")  # 仅 8002
family_app.include_router(admin_api_404_catchall)  # 家庭面普通 404，无后台语义
```