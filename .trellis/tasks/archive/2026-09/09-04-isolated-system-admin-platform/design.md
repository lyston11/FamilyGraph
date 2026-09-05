# 独立系统管理员后台与全业务监控平台：技术设计

## 1. Architecture boundary

### 1.1 Runtime topology

```text
family browser
  └─ family web :8080 (dev :5173)
       └─ /api → family API listener :8000

admin browser (VPN / loopback only)
  └─ admin web 127.0.0.1:8081 (dev :5174)
       └─ /admin-api → admin API listener :8002

api container
  ├─ family_app :8000  —— family auth + family business only
  └─ admin_app  :8002  —— system-admin auth + admin read/governance only
```

`admin_app` 与 `family_app` 在同一个后端进程/容器内共享 SQLAlchemy engine 和领域命令，但不共享路由注册表。两套 app 各自拥有中间件、OpenAPI、认证依赖和前缀；共享代码必须通过显式 service/schema 合同调用，不通过另一个 app 的 HTTP 路由调用。

### 1.2 Browser and token isolation

- family origin 与 admin origin 不同，浏览器 localStorage 天然隔离；两边使用不同的 key 前缀，不能使用相同 `fg.refresh_token`。
- family JWT 使用 family issuer/audience/secret；admin JWT 使用 `ADMIN_JWT_SECRET`、`ADMIN_JWT_ISSUER`、`ADMIN_JWT_AUDIENCE`。两种 token 互相拒绝，不能只依赖 `principal_type`。
- Admin JWT 必须同时满足签名、issuer、audience、`principal_type=system_admin`、账号 active、token version；family JWT 在 8002 的任意业务路由均拒绝。
- access token 只保存在对应前端内存；refresh token 只保存在对应 origin 的 localStorage 或更安全的 HttpOnly 方案，不跨 origin 共享。

## 2. Route and API contract

### 2.1 Family listener 8000

只注册家庭认证和家庭业务路由。不得注册：

- `/admin-api/*`；
- system-admin auth/bootstrap/change-password 路由；
- `admin_metadata`、`system_admin`、旧 `admin.py`、管理员 Agent 治理路由。

`/admin-api/*`、`/system-admin*` 等 HTTP 路径在家庭 web 与 family API 上统一普通 404，不返回 403 或专属错误码。

### 2.2 Admin listener 8002

所有后台请求使用 `/admin-api` 前缀：

```text
POST /admin-api/auth/login
POST /admin-api/auth/refresh
POST /admin-api/auth/logout
GET  /admin-api/auth/me
PUT  /admin-api/auth/password
PUT  /admin-api/auth/username
GET  /admin-api/health
GET  /admin-api/v1/overview
GET  /admin-api/v1/space-admins
GET  /admin-api/v1/space-admins/{id}/spaces
GET  /admin-api/v1/spaces/{id}
GET  /admin-api/v1/spaces/{id}/members
GET  /admin-api/v1/users/{id}/profile
GET  /admin-api/v1/users/{id}/avatar/thumbnail
GET  /admin-api/v1/spaces/{id}/relations
GET  /admin-api/v1/spaces/{id}/facts
GET  /admin-api/v1/operations/queue
GET  /admin-api/v1/operations/notifications
GET  /admin-api/v1/agent/runs
GET  /admin-api/v1/agent/jobs
GET  /admin-api/v1/audit/access
POST /admin-api/v1/access-sessions
POST /admin-api/v1/manager-applications/{id}/approve
POST /admin-api/v1/manager-applications/{id}/reject
```

实际实现可以合并资源，但必须保持前缀、认证域和 schema 隔离。列表接口必须有 `page`/`page_size` 上限、搜索词、状态/时间筛选和稳定排序；禁止无边界全量返回。

## 3. Authentication design

### 3.1 Admin account

建议新增/替换独立管理员凭据字段：

```text
system_admins
  id, username, status, created_at, updated_at
system_admin_accounts
  id, system_admin_id, password_hash, password_must_change,
  password_version, failed_attempts, locked_until, status,
  created_at, updated_at, claimed_at
system_admin_refresh_sessions
  id, system_admin_id, token_hash, rotated_from_id,
  expires_at, revoked_at, created_at, last_seen_at
```

本任务不实现旧 PIN 账号迁移或兼容登录；若部署数据库含无法识别的旧 system-admin 凭据，启动 preflight 必须 fail-closed 并要求受限运维处理，不把 PIN 静默当密码。

首次启动在受控 bootstrap service 中：

1. 在事务锁内确认不存在 active system admin；
2. 创建唯一 `username=admin`；
3. 生成强随机密码并只写入数据卷 `0600` 文件；
4. 标记 `password_must_change=true`；
5. 不写普通应用日志、审计 detail 或数据库明文；
6. 首次成功改密后删除凭据文件，删除失败则记录安全告警并拒绝标记完成。

忘记密码由受限 CLI 生成一次性恢复密码/文件，递增 `password_version`、撤销全部 refresh session，并要求首次登录改密。MFA 不在本任务内。

### 3.2 Token contract

```json
{
  "sub": "system-admin-id",
  "principal_type": "system_admin",
  "iss": "${ADMIN_JWT_ISSUER}",
  "aud": "${ADMIN_JWT_AUDIENCE}",
  "token_version": 3,
  "iat": 0,
  "exp": 0,
  "jti": "..."
}
```

Admin access 建议 15 分钟，refresh 轮换并设置绝对有效期；用户名/密码变更、锁定和恢复使旧版本立即失效。family JWT 不含后台身份字段；family response schema 只描述 family_user。

## 4. Admin read model

### 4.1 Aggregation root

后台查询从 active `space_admin` 关系出发：

```text
space_admin user
  └─ active managed spaces
       ├─ membership/profile health
       ├─ relation/confirmed fact health
       ├─ governance/notification backlog
       ├─ agent/job health
       └─ access/audit timeline
```

`owner_id`、用户在其他空间的角色、`is_admin` 或旧 platform role 不得作为目标空间管理员授权依据。无管理员、双管理员、锁定/删除管理员和关系不一致均进入异常队列，后台不能自动修复。

### 4.2 Schema policy

每个 admin response 使用专用 Pydantic schema 和显式列查询。禁止 `model_dump()` ORM 对象或复用家庭可见性 response。

- `AdminProfileOut`: `id/name/gender/birth/death/bio/avatar_thumbnail_url/profile_status/created_at`；头像 URL 必须是短期、管理员鉴权端点，不是文件路径。
- `AdminRelationOut`: 结构化端点、类型、状态、空间、时间和脱敏来源摘要；禁止 raw text、evidence 原文、message 和 private note。
- `AdminAttachmentMetadataOut`: id/type/title-safe/created_at（当前 `Attachment` 模型没有 `size` 字段，不得虚构）；禁止 `url_or_path`、description 原文和下载操作。
- `AdminAgentDiagnosticOut`: status/error_code/component/stack_location/retry/timing/sanitized_summary；服务端保存的原始 error_json 不直接序列化。

当前 User 没有 contact/address 等字段，本任务不新增；未来字段接入必须先更新 schema 和访问策略。地址、学校/单位、健康、未成年人敏感字段即使未来存在也不返回。

## 5. Sensitive access session and audit

### 5.1 Access session

```text
POST /admin-api/v1/access-sessions
request: { target_type: "user"|"space", target_id: int, reason: string }
response: { session_id, expires_at, target_type, target_id, allowed_scopes }
```

- reason 非空、长度受限、不得包含密码/token；服务端记录原始理由但做长度/控制字符校验。
- 会话绑定一个 user 或一个 space，TTL 30 分钟；不可跨目标、不可扩展为全后台会话。
- 敏感详情必须带 `X-Admin-Access-Session`；无票据、错目标、过期或已撤销返回统一 403。
- 前端只存内存；API 返回 `Cache-Control: no-store`，禁止 CDN/浏览器缓存。
- 每次使用都写审计；会话过期不删除审计记录。

### 5.2 Audit tables

新增独立 `admin_access_sessions` 与 `admin_access_audits`（或等价表）：

```text
admin_access_sessions
  id, system_admin_id FK, target_type, target_id, reason,
  scopes_json, issued_at, expires_at, revoked_at
admin_access_audits
  id, system_admin_id FK, session_id FK nullable, action,
  target_type, target_id, endpoint, filters_json, result_count,
  request_id, ip, created_at
```

两表永久保留；业务对象删除不级联删除审计。禁止保存响应正文、raw error、密码、token、Authorization、私人文本。登录/失败/登出和审批动作另写安全审计事件。

## 6. Governance write exception

`approve` / `reject` 走独立 command，使用 `require_system_admin`，目标申请不存在返回统一 404；approve 不要求理由，reject 要求非空理由，二次确认由 API payload + 服务端状态校验共同保证。单事务内完成状态、唯一 active `space_admin`、原管理员 consent 约束、domain event 和 audit。终态不可改判。

不注册旧 `backend/app/api/admin.py`。其中家庭 PIN 重置、档案修改、custody transfer、claim dispute、data-rights 和附件等能力不应通过包装/转发方式进入新 app。

## 7. Operational posture

- 8002 admin listener 只绑定容器 admin/backend 网络；生产不映射宿主端口。
- admin web 绑定 `127.0.0.1:8081`，由 VPN/内网反代；家庭 web 继续对外 `8080`。
- admin API 与 family API 使用不同健康检查、访问日志标签和限流策略；错误响应不泄露路由是否存在给家庭 listener。
- 原始 Agent 错误留在服务端受限诊断存储，脱敏器必须先移除 token/密钥/Authorization/prompt/message/PII，再生成 admin response。

## 8. Compatibility and rollback

- 家庭登录、家庭 PIN、家庭业务 API 保持现有合同，但移除后台身份字段和后台路由注册。
- 旧单 SPA 兼容路径不保留在家庭端；后台端口提供新入口。回滚只能整体恢复旧部署，不允许只恢复家庭 bundle 而留下不匹配的 admin API。
- 若独立 admin listener 无法启动，家庭 listener 仍可提供家庭服务，但部署应对后台健康检查报警；不要把后台流量降级到 8000。
