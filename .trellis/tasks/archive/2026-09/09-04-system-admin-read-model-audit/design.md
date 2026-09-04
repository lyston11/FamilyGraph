# 系统管理员全业务只读模型、访问票据与审计：技术设计

## 1. Scope / boundary

子任务 1 提供独立 `admin_app`、system_admin token 和 `/admin-api/auth/*`；本任务只实现 `/admin-api/v1` 的读模型、访问会话和审批唯一写例外。读模型不调用家庭 HTTP API，不让 system_admin 进入家庭 visibility 链。

## 2. Resource graph

```text
system_admin
  └─ overview
       └─ active space_admin
            └─ managed family spaces
                 ├─ members/profile projection
                 ├─ relation/confirmed-fact projection
                 ├─ operations/notifications/backlog
                 ├─ agent/job health
                 ├─ anomaly queue
                 └─ access audit timeline
```

目标空间管理员判定只查询 `SpaceMember(space_id, role='space_admin', status='active')`。同一家庭用户在不同空间分别聚合；没有管理员或违反唯一索引的不变量的空间只进入异常队列，不自动修复。

## 3. API signatures

### 3.1 Overview and operational lists

```text
GET /admin-api/v1/overview?page=1&page_size=50&search=&status=&from=&to=
GET /admin-api/v1/space-admins?page=1&page_size=50&search=&status=
GET /admin-api/v1/space-admins/{admin_user_id}/spaces?page=1&page_size=50
GET /admin-api/v1/spaces/{space_id}
GET /admin-api/v1/spaces/{space_id}/members?page=1&page_size=50&search=&status=
GET /admin-api/v1/spaces/{space_id}/relations?page=1&page_size=50&status=
GET /admin-api/v1/spaces/{space_id}/facts?page=1&page_size=50&state=confirmed
GET /admin-api/v1/operations/queue?page=1&page_size=50&kind=&status=
GET /admin-api/v1/operations/notifications?page=1&page_size=50&space_id=&read=
GET /admin-api/v1/agent/runs?page=1&page_size=50&space_id=&status=&from=&to=
GET /admin-api/v1/agent/jobs?page=1&page_size=50&space_id=&status=
GET /admin-api/v1/audit/access?page=1&page_size=50&target_type=&target_id=
```

每个列表返回 `{items, page, page_size, total, has_more}`；page_size 最大 100，查询必须有稳定排序。所有响应使用 admin 专用 schema，不能把 SQLAlchemy model 直接交给 Pydantic。

### 3.2 Profile, avatar and attachment metadata

```text
GET /admin-api/v1/users/{user_id}/profile
GET /admin-api/v1/users/{user_id}/avatar/thumbnail
GET /admin-api/v1/users/{user_id}/attachments?page=1&page_size=50
```

`AdminProfileOut` 只允许：`id,name,gender,birth,death,bio,avatar_available,profile_status,claim_status,created_at`（当前 `User` 模型没有 `updated_at`，不得虚构该字段）。头像 endpoint 重新鉴权、只输出缩略图、禁止暴露路径；附件只输出 `id,type,title_safe,created_at`（当前 `Attachment` 模型没有 `size` 字段，不得虚构），永不输出 url/path/description/download URL。

当前 User 没有联系方式字段；若未来增加 contact/email，不能自动加入 schema，必须先登记字段分类和票据 scope。address/school/health/minor-sensitive 永不加入。

### 3.3 Relations and facts

`AdminRelationOut` 允许端点 user id/name、`dir_class`、`status`、space id、label-safe、created/updated；`AdminFactOut` 允许 fact type、subject/object id/name、space id、state=confirmed、provenance-safe、timestamps、来源摘要。`RawRelationInput.text`、evidence 原文、private note、原始消息永不查询。

### 3.4 Agent diagnostics

`AdminAgentRunOut`/`AdminAgentJobOut` 允许状态、kind、attempt、timing、lease、error_code、component、stack_location、sanitized_summary、tool_name/resource_id；禁止 `content_json`、prompt、context、provider key、Authorization、result_json 和未处理 `error_json`。

脱敏器必须在 response schema 之前运行，采用字段黑名单 + 值模式清理：token/secret/key/authorization/prompt/message/content/email/phone/address 等键直接丢弃；字符串清理 credential-like、Bearer、URL query secret 和已知 PII pattern。原始错误只在受限服务端诊断通道保留。

## 4. Access sessions and audit contracts

### 4.1 Session API

```json
POST /admin-api/v1/access-sessions
{
  "target_type": "user|space",
  "target_id": 123,
  "reason": "处理该空间成员资料异常"
}
```

```json
200
{
  "session_id": "opaque-random",
  "target_type": "space",
  "target_id": 123,
  "allowed_scopes": ["profile.contact_masked", "relation.detail"],
  "issued_at": "...",
  "expires_at": "..."
}
```

理由必须非空、长度受限、无控制字符；session id 只存 hash。绑定目标只能是一个 user 或 space，TTL 30 分钟，不能升级/跨目标。敏感 endpoint 要求 `X-Admin-Access-Session`，无效统一 403。详情响应设置 `Cache-Control: no-store`。

### 4.2 Persistence

```text
admin_access_sessions(
  id, token_hash, system_admin_id FK, target_type, target_id,
  reason, scopes_json, issued_at, expires_at, revoked_at
)
admin_access_audits(
  id, system_admin_id FK, session_id FK nullable, action,
  target_type, target_id, endpoint, filters_json, result_count,
  request_id, ip, created_at
)
```

两表永久保留；业务删除不 cascade audit。普通查询和敏感详情每次写 audit；只写理由/范围/数量/请求信息，不写 response body、raw error、密码、token 或私密正文。读审计查询也受 system_admin auth 保护并分页。

## 5. Governance write exception

申请审批保留两个明确 endpoint：

```text
POST /admin-api/v1/manager-applications/{id}/approve
POST /admin-api/v1/manager-applications/{id}/reject
```

approve payload 可选 note；reject payload 必须非空 note；二次确认字段/幂等请求键由 API 层校验。调用现有 manager application command，在一笔事务内执行状态、consent、唯一 active admin、domain event 和独立 admin audit。终态重复裁决返回 409；目标不存在返回统一 404。除这两个 endpoint 外禁止业务写操作。

## 6. Validation and error matrix

| 情况 | 结果 |
|---|---|
| page_size > 100 / page < 1 | 422 |
| 未知 space/user/resource | 统一安全 404 或空列表，不泄露存在性 |
| 无敏感访问 session | 403 |
| session 过期/撤销/错目标/跨目标 | 403，审计拒绝尝试 |
| session reason 空/含控制字符 | 422 |
| response 含黑名单字段 | 测试失败，禁止发布 |
| raw Agent error 无法可靠脱敏 | 只返回 error_code + 安全位置，不返回摘要 |
| approve 终态/重复裁决 | 409 |
| reject 无理由 | 422 |
| 不存在申请 | 404 |

## 7. Test plan

- Schema exact-set tests for every response and nested item。
- Query tests for pagination, filters, stable sorting, safe empty, no N+1 and admin→spaces aggregation。
- Access session tests for TTL, hash-only persistence, target binding, no-store, replay/cross-target rejection and permanent audit。
- Sanitizer tests with secrets, Bearer tokens, prompts, messages, emails, phones, addresses, stack traces and safe diagnostics。
- Governance tests for approve/reject, consent, unique active admin, idempotency and forbidden writes。
- Registration tests ensure old `backend/app/api/admin.py` is absent and no family visibility dependency is used。

## 8. Wrong vs correct

### Wrong

```python
return User.query.get(user_id).__dict__
# exposes profile, credentials, private fields and storage paths
```

### Correct

```python
row = select(User.id, User.name, User.gender, User.birth, User.death,
             User.bio, User.profile_status, User.created_at)
return AdminProfileOut.model_validate(project(row))
```
