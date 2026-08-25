# 后端目录结构（初始规范 v0，M0 完成后以真实代码校正）

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口, PRAGMA 统一设置, 路由挂载, require_pin_changed 全局门禁
│   ├── config.py            # 环境变量/路径配置(DATA_DIR 派生 DATABASE_URL; SECRET_KEY 缺失拒绝启动)
│   ├── errors.py            # 业务错误码常量表(MACHINE_CODE)
│   ├── logctx.py            # request_id/user_id 结构化日志上下文
│   ├── db.py                # SQLAlchemy engine/session, WAL/FK/busy_timeout PRAGMA
│   ├── models/              # SQLAlchemy ORM, 一表一文件(user/account/audit_log/auth_challenge/refresh_session)
│   ├── schemas/             # Pydantic 请求/响应模型, 一域一文件(auth)
│   ├── api/                 # 路由层: auth.py users.py bootstrap.py health.py deps.py(认证依赖/PIN 门禁)
│   ├── services/            # 业务逻辑(路由层禁止直接写业务): auth_guard 限流锁定,
│   │                        #   challenge 同名消歧(单次使用防重放), refresh_session 轮换+重用检测,
│   │                        #   audit 唯一审计入口, bootstrap 首启初始化; visibility.py 于 M2 落位
│   └── utils/               # security.py(pin secrets 生成/bcrypt/JWT), timeutil.py
├── migrations/              # Alembic(env.py 从 app.config 注入 URL)
├── tests/                   # pytest; test_authz_matrix.py 为 IDOR 矩阵测试家(M2); conftest 每次迁移往返
└── pyproject.toml           # 锁版本
```

规则：
- 分层单向依赖 api → services → models；api 层只做参数校验与调用 service。
- 所有跨用户数据出口必须经 `services/visibility.py`，禁止在路由里内联可见性 if。
