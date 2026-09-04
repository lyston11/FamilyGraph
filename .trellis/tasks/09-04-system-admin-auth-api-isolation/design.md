# 系统管理员密码认证与独立 Admin API Listener：技术设计

## 1. Scope / trigger

当前公开 `app` 同时注册家庭和 system-admin 路由，认证响应复用家庭 `UserOut`/PIN 语义。要让家庭端完全不知道后台，必须在 listener、路由注册、凭据模型、JWT 签发域和 OpenAPI 层同时隔离。

## 2. Listener topology

```text
family_app :8000
  /api/auth/login (family only)
  /api/... family routes

admin_app :8002
  /admin-api/auth/login
  /admin-api/auth/refresh
  /admin-api/auth/logout
  /admin-api/auth/me
  /admin-api/auth/password
  /admin-api/auth/username
  /admin-api/health
```

沿用 `backend/app/serve.py` 的双 listener 生命周期、端口预检和优雅停机模式；admin_app 与 family_app 共享 engine/lifespan，但不共享 router 对象。公开 app 对 `/admin-api/{rest:path}` 使用不带后台语义的 404 catch-all。

## 3. Data schema and signatures

新增或替换系统管理员凭据字段：

```python
POST /admin-api/auth/login
  {"username": str, "password": str}
  -> {"access_token": str, "refresh_token": str, "token_type": "bearer", "admin": AdminSessionOut}

PUT /admin-api/auth/password
  {"current_password": str, "new_password": str}
  -> AdminSessionOut

PUT /admin-api/auth/username
  {"current_password": str, "username": str}
  -> AdminSessionOut
```

`AdminSessionOut` 只含 `id/username/password_must_change/status`，不含 password_hash、failed_attempts、locked_until 细节、refresh token 或安全密钥。

数据库：

```text
system_admins(id, username, status, created_at, updated_at)
system_admin_accounts(
  id, system_admin_id, password_hash, password_must_change,
  password_version, failed_attempts, locked_until, status,
  created_at, updated_at, claimed_at
)
system_admin_refresh_sessions(
  id, system_admin_id, token_hash, rotated_from_id,
  expires_at, revoked_at, created_at, last_seen_at
)
```

通过 Alembic 新 revision 建立合同。检测到无法识别的旧 PIN 凭据时 fail-closed；不实现生产账号迁移。

## 4. Bootstrap and recovery

启动 preflight 在事务锁内：

1. 查询 active system_admin；存在则不生成新账号；
2. 不存在则创建唯一 username `admin`；
3. 用 CSPRNG 生成强随机密码；
4. 原子写入 `DATA_DIR/bootstrap/admin-credentials`，创建后 chmod 0600；
5. 数据库只存哈希，普通日志不打印密码；
6. 首次改密事务提交后删除文件；删除失败写安全告警并不清除 `password_must_change`。

运维恢复命令必须复用同一 password service，生成一次性恢复密码、递增 password_version、撤销所有 refresh session 并写安全审计；密码本身只落 0600 文件。

## 5. JWT and session validation

```json
{
  "sub": "system-admin-id",
  "principal_type": "system_admin",
  "iss": "${ADMIN_JWT_ISSUER}",
  "aud": "${ADMIN_JWT_AUDIENCE}",
  "token_version": 2,
  "jti": "uuid",
  "iat": 0,
  "exp": 0
}
```

8002 依次校验签名、issuer、audience、principal_type、账号 active、password_version/token_version 和 session 状态。family JWT 使用另一组 family issuer/audience/secret，不能被 admin dependency 接受。

## 6. Validation and error matrix

| 条件 | 行为 |
|---|---|
| username/password 错误 | 401 统一 `ADMIN_INVALID_CREDENTIALS`，不泄露账号存在性 |
| 失败次数达到阈值 | 429 + Retry-After；仍使用统一文案 |
| `password_must_change=true` | 只允许 password、refresh、logout；其它 admin API 403 |
| 缺失/弱 `ADMIN_JWT_*` | 启动失败，不使用默认密钥 |
| family token → 8002 | 401/403 统一管理员认证失败 |
| admin token → 8000 family route | 401/403 家庭认证失败，不回退主体 |
| 过期/错误 audience/token version | 401，撤销本地会话 |
| bootstrap 已完成 | 不生成第二账号，受控启动状态记录 |
| 0600 文件删除失败 | 安全告警，首次改密流程保持未完成 |

## 7. Good / base / bad cases

- Good：空库启动生成 `admin`，密码只在 0600 文件出现；登录后改密，文件消失，旧 refresh 立即失效。
- Base：已初始化部署重启不生成第二账号，家庭 API 仍正常服务，8002 admin API 独立可用。
- Bad：把 `admin/admin` 写入代码、把 PIN 当密码、在 family app 中 import admin router、把 admin JWT 仅靠 `principal_type` 放行到共享 audience。

## 8. Tests required

- App route registration：8000 无 admin 路由，8002 只含 admin 路由。
- Credential bootstrap：唯一账号、随机密码文件内容、0600、日志/DB 不含明文、改密后删除。
- Auth matrix：无 token、family token、错误 issuer/audience/principal、过期 token、锁定、版本失效。
- Refresh/logout：轮换、绝对有效期、重复使用旧 refresh 拒绝、撤销主体正确。
- Password lifecycle：首次强制改密、用户名/密码修改、运维恢复、旧会话全部失效。
- Family regression：名字+PIN、家庭 response schema、家庭错误文案不回归。

## 9. Wrong vs correct

### Wrong

```python
app.include_router(system_admin_router, prefix="/api")
# family frontend and admin frontend both call /api/auth/login
```

### Correct

```python
family_app.include_router(family_auth_router, prefix="/api")
admin_app.include_router(admin_auth_router, prefix="/admin-api")
# each dependency validates its own issuer/audience and principal
```
