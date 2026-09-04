# Current admin boundary research

## Repository baseline (2026-09-04)

### Frontend

- `frontend/src/main.ts` 创建单一 Vue app，注册单一 Pinia、router 和 auth interceptor。
- `frontend/src/router/index.ts` 把家庭路由和 `/system-admin`、`/system-admin/login` 放在同一 `createWebHistory()` router；守卫用 `principal_type` 互斥，但 family_user 访问管理员登录页会回家庭空间。
- `frontend/src/App.vue` 根据 auth/route 在同一 app 内切换 `AppShell` 与 `SystemAdminShell`。
- `frontend/src/stores/auth.ts` 使用唯一 `fg.refresh_token` localStorage key；access token 内存态；system-admin/family_user 只由 `user.principal_type` 区分。
- `frontend/vite.config.ts` 只有一个开发 server 和 `/api → http://localhost:8000` proxy。
- `frontend/src/api/admin.ts` 同时包含最小治理 API 和历史 break-glass client；`SystemAdminView.vue` 使用最小治理查询。

### Deployment

- `docker-compose.yml` 只有 `web` 服务映射 `8080:80`，api 只发布 `8000:8000`。
- `frontend/nginx.conf` 只有一个 server，`/api/` 反代 `api:8000`；不存在 admin web/admin API listener。
- `frontend/Dockerfile` 只构建单一 dist。

### Backend

- `backend/app/main.py` 公开 `app` 注册家庭路由、`system_admin_router`、`admin_metadata_router` 和其他 admin 路由；旧 `app.api.admin` 当前未注册，这是必须保持/进一步收紧的安全基线。
- `backend/app/serve.py` 已实现公开 listener 与 internal agent listener 的双 listener 启动模式，可复用其生命周期和端口预检思路。
- `backend/app/api/auth.py` 当前共享 `/api/auth/login`，先匹配独立 `SystemAdmin`/`SystemAdminAccount`，但仍返回兼容 `UserOut` 和 PIN 语义。
- `backend/app/models/system_admin.py` 当前是 `SystemAdmin`、`SystemAdminAccount`、`SystemAdminRefreshSession`，账号字段仍有 `pin_hash`、`pin_must_change`、`token_version`。
- `backend/app/api/admin_metadata.py` 已有 `require_system_admin` 的账号、空间、管理员、成员和交接最小查询，但不覆盖全业务监控。
- `backend/app/api/system_admin.py` 已有 `require_system_admin` 的申请队列/裁决。
- `backend/app/api/admin.py` 含旧家庭用户列表、PIN 重置、档案修改、审计、owner 邀请、数据权利和争议等高风险混合能力，不得直接搬运。

### Data model constraints

- `User` 当前字段为 name/gender/birth/death/bio/avatar_path/privacy/profile status 等；没有电话、邮箱、地址、学校/单位、健康字段。
- `Relation` 存结构关系边；`SourceFact` 存结构化事实和 `raw_text_id`；`RawRelationInput.text` 是私人原文，后台禁止返回。
- `Attachment` 含 `url_or_path` 与 description，后台只能投影安全元数据。
- `AgentSession`/`AgentRun`/`AgentJob`/`AgentToolCall` 含消息、错误 JSON、工具结果等，后台只可读安全运行元数据和脱敏诊断。
- `AuditLog.actor_id` FK 指向家庭 users，独立管理员读取审计需要新表直接 FK system_admin。
- `SpaceMember.role` 的有效业务角色为 `space_admin|member`，管理员聚合必须按目标空间 active `space_admin`。

## Research conclusion

当前实现可以复用数据库 engine、领域 commands、`serve.py` 双 listener 生命周期和现有 system-admin 主体表的概念，但不能复用单 SPA、共享 `/api`、共享 token key、共享 JWT 签发域、旧 admin.py 或家庭 visibility response。要满足产品目标，必须按父任务四个子任务依次拆分认证 listener、只读 read model/审计、独立后台 frontend 和部署/家庭清理。