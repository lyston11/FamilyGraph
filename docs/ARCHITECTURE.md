# FamilyGraph 系统架构与设计

> 本文是项目的高层架构与设计总览，反映 2026-09 独立系统管理员平台（09-04 任务树）落地后的现状。
> 可执行合同（API 签名、错误矩阵、测试清单）的权威来源是 `.trellis/spec/architecture.md`，本文与其保持同步；两者冲突时以 spec 为准并修订本文。

## 1. 系统总览

现代家谱协作平台，由三个产品面组成：

```text
┌─────────────────────────┐        ┌──────────────────────────────┐
│ 家庭用户端（唯一公开面）  │        │ 系统管理员后台（仅运维可见）    │
│ frontend/               │        │ system-admin-frontend/       │
│ 家庭账号：名字 + PIN     │        │ 管理员账号：用户名 + 强密码    │
│ 家谱协作 / 档案 / 关系    │        │ 全业务只读监控 + 申请审批      │
└───────────┬─────────────┘        └──────────────┬───────────────┘
            │ /api                                │ /admin-api
            ▼                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ api 容器（单进程三 listener，共享 engine/lifespan，不共享路由）      │
│  family_app :8000 ── 家庭认证 + 家庭业务                          │
│  internal   :8001 ── agent sidecar 内部协议（不发布宿主）           │
│  admin_app  :8002 ── 管理员认证 + /admin-api/v1 只读模型（不发布宿主）│
└───────────────────────────────┬─────────────────────────────────┘
                                │ /internal/agent
                                ▼
                       agent sidecar（模型工具执行）
```

- **家庭用户端完全不知道系统管理员的存在**：家庭 bundle、路由表、OpenAPI、认证响应、错误路径中不含任何后台痕迹；未注册深链（`/system-admin`、`/admin`、`/admin-api/*`）统一普通 404。
- **后台是独立产品面**，不是家庭 SPA 中的隐藏角色：独立前端项目、独立端口与 origin、独立浏览器存储、独立 JWT 签发域、独立 API listener 与 Docker 网络。
- **Agent**（V2.1）：sidecar 容器执行模型工具调用；模型 API key 不出后端（ProviderGateway 代理），与 api 之间走 internal 8001 协议。

## 2. 运行拓扑与端口矩阵

| 面 | 开发 | 生产 | 说明 |
|---|---|---|---|
| 家庭前端 | `5173`（Vite，代理 `/api`→8000） | `8080`（nginx 容器） | 唯一公开 Web 面 |
| 家庭 API | `8000`（uvicorn） | `8000`（发布宿主） | `/api/*` |
| agent 内部协议 | `8001` | 仅 backend 网络接口 `172.28.0.10` | 不发布宿主 |
| 管理员前端 | `5174`（Vite，代理 `/admin-api`→8002） | `127.0.0.1:8081`（nginx 容器） | 仅回环，VPN/内网反代进入 |
| 管理员 API | `8002` | 仅 admin 网络接口 `172.29.0.10` | **不发布宿主** |

Docker 网络隔离（compose）：

- `frontend`：web ↔ api（对外出口）。
- `backend`（internal:true）：api ↔ agent sidecar；sidecar 无外网 egress。
- `admin`（internal:true）：admin-web ↔ api 的 8002 接口；宿主/公网/家庭 web 均不可路由。

Nginx 合同：家庭 nginx 对 `/admin-api/`、`/system-admin`、`/admin` 返回普通 404，仅代理 `/api/`；后台 nginx 仅代理 `/admin-api/` → `api:8002`，对 `/api/`、`/internal/` 返回 404。

## 3. 认证与主体模型

### 3.1 两个互斥的主体域

| | 家庭用户 | 系统管理员 |
|---|---|---|
| 主体模型 | `users` + `accounts`（名字 + PIN） | `system_admins` + `system_admin_accounts`（用户名 + 密码哈希） |
| API 面 | 8000 `/api/auth/*` | 8002 `/admin-api/auth/*` |
| JWT 签发域 | `SECRET_KEY` + family issuer/audience | `ADMIN_JWT_SECRET` + `ADMIN_JWT_ISSUER/AUDIENCE`（缺失/过弱拒启） |
| 会话存储 | `fg.refresh_token`（家庭 origin） | `fg.admin.refresh_token`（后台 origin） |
| access token | 内存 | 内存 |
| 首登 | PIN 首改（家庭合同不变） | `password_must_change` 强制改密 |

- JWT claims 携带 `principal_type`（family 侧恒 `family_user`；admin 侧 `system_admin`）+ `token_version` + `jti`；**issuer/audience/secret 物理隔离**——把任一方 token 复制到另一个 listener 必然被拒，不能只靠 `principal_type` 区分。
- 会话撤销触发器：改密码/用户名、锁定、运维恢复 → `password_version+1` 且 refresh session 全撤销；refresh 轮换不续期（绝对有效期）。
- 登录失败达到阈值锁定返回 429；错误文案统一，不泄露账号存在性。

### 3.2 管理员账号生命周期（无公开初始化）

- 部署启动 preflight 在事务锁内保证唯一 active 管理员：空库时创建用户名 `admin`，CSPRNG 随机强密码**只**写入数据卷 `/data/bootstrap/admin-credentials`（`0600`，原子写）。
- 首次登录强制改密；改密事务提交后删除凭据文件，删除失败写安全告警且不标记完成。
- 忘记密码走受限运维恢复：`docker compose exec api python -m app.admin_recovery`（一次性密码只落 0600 文件，旧会话全撤销）。
- 密码只存哈希；初始/恢复密码不进日志、数据库明文或任何前端产物。MFA 为后续任务。

## 4. 后台数据边界（只读 + 唯一写例外）

### 4.1 信息架构

后台以 **active `space_admin` 为聚合根**：空间管理员 → 其管理的家族空间 → 成员/档案/关系/已确认事实 → 空间健康与治理状态。授权与聚合只认 `SpaceMember(role='space_admin', status='active')`；`owner_id`、`is_admin`、其他空间角色、家庭 visibility 链一律不作后台依据。无管理员/双管理员/锁定或被删管理员的空间进入**异常队列**，只读展示、不自动修复。

### 4.2 数据分级

**允许（普通读取 + 审计）**：账号与空间运营状态、成员关系、基础档案（姓名/生卒/性别/简介/档案状态）、结构化关系边与已确认事实、附件安全元数据（id/type/title_safe/created_at）、运营队列与通知、Agent/job 运行元数据（状态/耗时/重试/错误码/工具名/资源 ID）。

**敏感详情（需 30 分钟目标绑定访问会话 + 理由）**：单用户/单空间档案钻取、头像缩略图（专用鉴权端点，不暴露路径）、附件列表；响应 `Cache-Control: no-store`，票据只存后台前端内存。

**永久禁止**：PIN/密码哈希、JWT/refresh token、`SECRET_KEY`/`ADMIN_JWT_*`、Memory/Session/AgentMessage/RAG 原文、prompt 与模型上下文、`RawRelationInput.text` 与关系证据原文、附件原文件与 `url_or_path`、精确地址/学校单位/健康信息/未成年人敏感字段、批量导出与全库快照。Agent 原始错误仅服务端保留，API 只返回二次脱敏诊断（脱敏器 fail-closed：不可靠即只给 error_code + 安全位置）。

**合同红线**：所有响应走专用 Pydantic schema（`extra="forbid"`）+ 显式列投影，禁止 ORM 直接序列化或复用家庭 visibility API。字段白名单以模型实况为准（如 `User` 无 `updated_at`、`Attachment` 无 `size`，不得虚构）。

### 4.3 审计与审批

- 独立审计表 `admin_access_sessions` / `admin_access_audits` 直接 FK `system_admin_id`；记录 reason/scopes/endpoint/filters/result_count/request_id/IP；永久保留（业务删除 SET NULL 不级联）；不保存响应正文与敏感值。
- **唯一业务写例外**：空间管理员申请 approve（理由可选）/ reject（理由必填），均需显式二次确认；复用既有单事务命令（状态 + 原管理员 consent + 唯一 active `space_admin` + 领域事件 + 双侧审计）；终态不可改判（重复裁决 409）。不触碰 `family_spaces.owner_id`；旧 `backend/app/api/admin.py` 的 break-glass 能力永不注册。

## 5. 前端架构

```text
frontend/                    家庭 SPA（Vue3 + Vite + TS + Pinia + Naive UI）
system-admin-frontend/       后台 SPA（同技术栈，独立项目与构建）
```

- **零共享**：两个 SPA 不互相 import（CI 有模块图断言）；API client、store、router、types、构建产物完全独立。
- 认证合同：登录响应硬校验主体/白名单字段后才落会话；`password_must_change` 守卫只放行改密页；session expired 回各自登录页；redirect 只接受站内白名单。
- 后台交互合同：敏感详情先弹理由表单 → 创建访问会话 → 票据仅内存（`X-Admin-Access-Session`）；过期/403 引导重新授权；审批是唯一写 UI（二次确认 + 不可逆提示）。
- Agent 监控 5 秒轮询，`document.hidden` 暂停，卸载清理 timer 与 in-flight 请求。

## 6. 后端分层

```text
backend/app/
├── main.py          # 三 app（family/internal/admin）+ lifespan preflight + 普通 404 catch-all
├── serve.py         # 三 listener 启动/端口预检/共享信号优雅停机
├── api/             # 路由层：家庭业务（users/spaces/graph/...）
│                    #   admin_auth.py admin_deps.py        → 8002 认证与门禁
│                    #   admin_read.py admin_governance.py   → 8002 只读模型 + 审批
│                    #   admin_agent.py controlled_web.py    → platform_operator 既有面（8000）
│                    #   admin.py（旧 break-glass，永不注册，仅作回归断言对象）
├── services/        # 领域服务：visibility（家庭授权单点）
│                    #   admin_auth / admin_bootstrap / admin_read_model
│                    #   admin_access_sessions / admin_audit / admin_sanitizer
├── models/          # ORM：业务表 + system_admin + admin_access（0028/0029）
├── schemas/         # Pydantic：admin_read.py 为字段白名单唯一来源
└── utils/           # security.py（家庭 PIN/JWT）；admin_security.py（admin JWT 签发域）
```

规则：api → services → models 单向依赖；家庭数据出口必须经 `visibility.py`；admin 读模型禁止复用家庭 visibility 链。Alembic 迁移链 `0001→0029`；对历史凭据结构 fail-closed，不做静默数据转换。

## 7. 安全不变量（改任何代码前先读）

1. 家庭端零后台痕迹：源码、bundle、source map、路由表、OpenAPI 不得出现 `system_admin`/`admin-api`/后台端口等字符串（构建后有扫描）。
2. 未注册路径统一普通 404，不用 403/重定向/自定义错误页（存在性 oracle 红线）。
3. 8002 不发布宿主；admin 网络仅 admin-web 可达；生产 admin web 仅回环绑定。
4. 两套签发域互拒；`ADMIN_JWT_*` 缺失或与家庭 `SECRET_KEY` 同值即拒启，无开发逃逸。
5. 密码/token/secret/私人正文/证据原文/附件原文永不进日志、审计正文、HTTP 响应或前端。
6. 后台除审批外无任何写端点（路由注册断言守护）；旧 `admin.py` 永不注册。
7. 敏感详情必须带 30 分钟目标绑定会话 + 理由，全部读取留痕。
8. 字段白名单精确集合断言；模型没有的字段不得虚构。

## 8. 质量门禁

```bash
cd backend                 && .venv/bin/python -m pytest -q && ruff check . && ruff format --check . && mypy app
cd frontend                && npm run type-check && npm run lint && npm test && npm run build
cd system-admin-frontend   && npm run type-check && npm run lint && npm test && npm run build
```

回归红线测试（节选）：授权矩阵 IDOR（`test_authz_matrix.py`）、双 listener 路由注册与旧 admin.py 未注册、交叉签发域拒绝、凭据文件 0600 生命周期、字段白名单精确集合、访问会话拒绝矩阵、家庭 dist 禁止字符串扫描。
