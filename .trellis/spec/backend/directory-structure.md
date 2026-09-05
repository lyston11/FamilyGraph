# 后端目录结构（2026-09-05 按真实代码校正；09-04 独立系统管理员平台落地后）

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口：family_app(8000) / internal_app(8001) / admin_app(8002)
│   │                        #   lifespan 启动 preflight（管理员 bootstrap / 旧结构 fail-closed）
│   │                        #   家庭面对 /admin-api/* 的普通 404 catch-all
│   ├── serve.py             # 三 listener 启动：端口预检、共享信号、优雅停机
│   ├── admin_recovery.py    # 运维恢复 CLI：python -m app.admin_recovery（0600 一次性密码文件）
│   ├── config.py            # 环境变量/路径配置(DATA_DIR 派生 DATABASE_URL; SECRET_KEY/ADMIN_JWT_* 缺失拒启)
│   ├── errors.py            # 业务错误码常量表(MACHINE_CODE；ADMIN_* 管理员域)
│   ├── logctx.py            # request_id/user_id 结构化日志上下文
│   ├── db.py                # SQLAlchemy engine/session, WAL/FK/busy_timeout PRAGMA
│   ├── models/              # SQLAlchemy ORM 一表一文件：user/account/space/relation/
│   │                        #   relationship_facts/attachment/agent/agent_provider/memory*/rag/
│   │                        #   notification/steward/personal_family_view/controlled_web/
│   │                        #   system_admin(0028 密码凭据)/admin_access(0029 会话+审计)
│   ├── schemas/             # Pydantic 一域一文件；admin_read.py = 后台字段白名单唯一来源(extra=forbid)
│   ├── api/                 # 路由层（见下）
│   ├── services/            # 业务逻辑（见下）
│   └── utils/               # security.py(家庭 PIN/bcrypt/JWT)；admin_security.py(admin 独立 JWT 签发域)
├── backup.py cleanup.py     # 运维: python -m app.backup / python -m app.cleanup
├── migrations/              # Alembic(env.py 注入 URL; 迁移链 0001→0029)
├── tests/                   # pytest; test_authz_matrix.py IDOR 矩阵; test_system_admin_boundary.py
│                            #   双 listener 路由注册/交叉拒绝/凭据文件; conftest 每次迁移往返
└── pyproject.toml
```

api/ 分工（8000 家庭面 / 8001 internal / 8002 admin 面）：

- 家庭 8000：auth users spaces members connections graph attachments memory notifications
  household_card personal_family_view family_recommendations kinship action_cards governance
  controlled_web misc(lunar/stats/search) health bootstrap(status only) deps(家庭认证+PIN 门禁)
  admin_agent（platform_operator 专属 Agent Provider 治理，既有面）
- internal 8001：internal_agent（agent sidecar 协议）
- admin 8002：admin_auth(登录/refresh/logout/me/改密/改用户名) admin_deps(require_admin_ready/
  enforce_access_session 门禁) admin_read(/admin-api/v1 只读模型+访问会话) admin_governance
  (审批唯一写例外)
- admin.py：旧家庭 break-glass 路由集合，**永不注册**；仅作为"路径不存在"回归断言对象

services/ 分工：visibility(家庭授权单点) auth_guard challenge refresh_session audit custody
relation_fsm kinship space_fsm attachments lunar steward memory/rag controlled_web …；
admin_auth(登录/锁定/refresh 轮换/凭据变更) admin_bootstrap(0600 凭据文件 preflight)
admin_read_model(后台只读查询) admin_access_sessions(30 分钟目标绑定票据)
admin_audit(admin_access_audits 唯一写入口) admin_sanitizer(响应前 fail-closed 脱敏)

规则：
- 分层单向依赖 api → services → models；api 层只做参数校验与调用 service。
- 所有跨用户数据出口必须经 `services/visibility.py`，禁止在路由里内联可见性 if。
- admin 读模型禁止复用家庭 visibility 链；响应用专用 schema 显式列投影。
