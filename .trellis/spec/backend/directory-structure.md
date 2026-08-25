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
│   ├── api/                 # 路由层: auth users members connections graph spaces attachments
│   │                        #   bootstrap health misc(lunar/stats/search) admin deps(PIN门禁)
│   ├── services/            # 业务逻辑: auth_guard challenge refresh_session audit bootstrap
│   │                        #   custody(代管权) relation_fsm(世代一致性) kinship space_fsm
│   │                        #   visibility(授权单点,M2) attachments(m3a校验链) lunar
│   └── utils/               # security.py(pin secrets 生成/bcrypt/JWT), timeutil.py
├── backup.py cleanup.py     # 运维: python -m app.backup / python -m app.cleanup
├── migrations/              # Alembic(env.py 注入 URL; 迁移链 0001→0007)
├── tests/                   # pytest; test_authz_matrix.py 为 IDOR 矩阵测试家(M2); conftest 每次迁移往返
└── pyproject.toml           # 锁版本
```

规则：
- 分层单向依赖 api → services → models；api 层只做参数校验与调用 service。
- 所有跨用户数据出口必须经 `services/visibility.py`，禁止在路由里内联可见性 if。
