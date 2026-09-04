# 系统管理员全业务只读模型、访问票据与审计

> 前置依赖：子任务 1（`09-04-system-admin-auth-api-isolation`）已提供独立 `admin_app`、system_admin token 与 `/admin-api/auth/*`。本任务只实现 `/admin-api/v1` 的读模型、访问会话、审计与审批唯一写例外。

## Goal

在 8002 的 `/admin-api/v1` 上建立系统管理员全业务只读查询面：以 active `space_admin` 为聚合根，按“空间管理员 → 其管理的家族空间 → 空间健康与治理状态”组织数据；敏感详情通过绑定单个 user/space 的 30 分钟访问会话获取；所有读取写入独立审计表。空间管理员申请审批是唯一业务写例外。后台不得读取认证秘密、私人正文和高敏感档案。

## Requirements

### RM-F1：读模型与信息架构

- 端点（全部位于 8002 `/admin-api/v1`）：
  - `GET /overview`、`GET /space-admins`、`GET /space-admins/{admin_user_id}/spaces`
  - `GET /spaces/{space_id}`、`GET /spaces/{space_id}/members`、`GET /spaces/{space_id}/relations`、`GET /spaces/{space_id}/facts`
  - `GET /users/{user_id}/profile`、`GET /users/{user_id}/avatar/thumbnail`、`GET /users/{user_id}/attachments`
  - `GET /operations/queue`、`GET /operations/notifications`
  - `GET /agent/runs`、`GET /agent/jobs`
  - `GET /audit/access`
- 聚合根判定只查询 `SpaceMember(space_id, role='space_admin', status='active')`；同一家庭用户在不同空间分别聚合。
- `owner_id`、`is_admin`、用户在其他空间的角色、家庭 visibility 链一律不得作为授权或聚合依据。
- 列表接口必须支持分页（`page`/`page_size`，page_size 上限 100）、搜索、状态/时间筛选和稳定排序；返回 `{items, page, page_size, total, has_more}`；禁止无边界全量返回和批量导出。
- 无 active 管理员、双管理员、管理员被锁定/删除、角色关系不一致的空间进入独立异常队列；后台只读展示，不自动修复。

### RM-F2：字段白名单（数据分级）

允许返回：

- 档案：`id, name, gender, birth, death, bio, avatar_available(或鉴权缩略图 URL), profile_status, claim_status, created_at`。
- 关系：结构化关系边（端点、`dir_class`、`status`、space、label-safe、时间）与已确认事实（fact type、subject/object、state=confirmed、provenance-safe、时间）。
- 附件：仅安全元数据 `id, type, title_safe, created_at`；头像通过 8002 专用鉴权缩略图端点返回。
- Agent/job：状态、kind、attempt、timing、lease、`error_code`、component、stack_location、sanitized_summary、`tool_name`、资源 ID。
- 运营：通知、申请、交接、ActionCard 状态、统计与异常元数据。

明确禁止返回：

- PIN/密码明文或哈希、JWT、refresh token、`SECRET_KEY`/`ADMIN_JWT_SECRET`、Authorization header。
- Memory、Session、AgentMessage、RAG 原文、prompt、模型上下文、工具结果正文。
- `RawRelationInput.text`、关系证据原文、私人描述。
- 附件原文件、`url_or_path`、附件 description 正文、下载链接。
- 精确地址、学校/单位、健康信息、未成年人敏感字段（当前模型无这些字段，本任务不新增；未来电话/邮箱接入时沿用列表遮罩 + 访问会话合同）。
- Agent 原始 `error_json`/`result_json`：服务端可保留原文用于排障，API 只返回二次脱敏诊断（移除 token/secret/key/Authorization/prompt/message/content/email/phone/address、Bearer、URL query secret、已知 PII pattern）。

### RM-F3：访问会话（敏感详情票据）

- `POST /admin-api/v1/access-sessions`：`{target_type: "user"|"space", target_id, reason}`；reason 非空、长度受限、无控制字符。
- 会话绑定单个 user 或单个 space，TTL 30 分钟；不可跨目标复用、不可升级为全后台会话。
- 敏感详情必须携带访问会话票据（如 `X-Admin-Access-Session`）；无票据、错目标、过期、撤销统一 403。
- 会话 id 只存 hash；票据只在前端内存保存；详情响应设置 `Cache-Control: no-store`。
- 每次使用都写审计；会话过期不删除审计记录。

### RM-F4：独立审计表

- 新增 `admin_access_sessions` 与 `admin_access_audits`，直接 FK `system_admin_id`（不借用家庭 `audit_log.actor_id`）。
- 审计字段：system_admin_id、session_id、action、target_type、target_id、endpoint、filters summary、result_count、request_id、IP、timestamp、reason。
- 两表永久保留；业务对象删除不级联删除审计；不保存响应正文、密码、token、原始错误或私人文本。
- 登录/失败/登出和审批动作另写安全审计事件；读审计查询本身受 admin auth 保护并分页。

### RM-F5：审批唯一写例外

- `POST /admin-api/v1/manager-applications/{id}/approve`：理由可选，必须二次确认。
- `POST /admin-api/v1/manager-applications/{id}/reject`：理由必填非空，必须二次确认。
- 单事务内完成申请状态、原管理员 consent、唯一 active `space_admin`、domain event 和独立 admin audit；终态不可改判（重复裁决 409，目标不存在统一 404）。
- 不修改 `family_spaces.owner_id`；不提供 PIN 重置、档案修改、删除/恢复、数据权利 break-glass、争议决议、导出、附件下载或 Agent 控制写操作。

## Out of scope

- 后台前端 UI（子任务 3）。
- 部署拓扑与家庭端清理（子任务 4）。
- 电话/邮箱/地址等新 User 字段。
- 异常队列自动修复、批量导出、全库快照。

## Acceptance Criteria

- [ ] 每个响应 schema 有精确字段集合断言；禁止字段（含 ORM 直接序列化）出现在任何响应中。
- [ ] 分页上限、非法分页参数、筛选条件、稳定排序、防存在性枚举（未知 space/user 返回安全 404/空列表）测试通过。
- [ ] 管理员 → 所管空间聚合查询正确，无 N+1；无管理员/双管理员/锁定管理员空间进入异常队列。
- [ ] 敏感详情：无票据、错目标、过期票据、跨管理员复用、跨目标复用全部 403 且审计拒绝尝试；响应带 `Cache-Control: no-store`。
- [ ] 访问会话 reason 校验、TTL 30 分钟、hash-only 持久化生效。
- [ ] 审计永久保留、可分页查询、不含业务正文/响应正文/敏感值。
- [ ] Agent 错误脱敏器测试通过：secrets、Bearer、prompt、message、email、phone、address、堆栈均不泄漏；无法可靠脱敏时只返回 error_code + 安全位置。
- [ ] approve/reject：二次确认、理由约束、单事务、consent、唯一 active admin、409/404 语义测试通过；除审批外无任何业务写端点。
- [ ] 旧 `backend/app/api/admin.py` 未注册；读模型未使用家庭 visibility API。
- [ ] `ruff check`、`ruff format --check`、`mypy`、后端全量 pytest 通过。

## Notes

- 设计细节见 `design.md`（API 签名、访问会话、审计表、脱敏器）；实施顺序见 `implement.md`；当前读模型基线见 `research/current-read-models.md`。
- 注意：设计中的 `AdminProfileOut.updated_at` 与 `AdminAttachmentMetadataOut.size` 已修正移除——当前 `User` 模型没有 `updated_at`，`Attachment` 模型没有 `size`，不得为不存在的字段伪造实现。
