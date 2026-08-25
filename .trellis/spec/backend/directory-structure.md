# 后端目录结构（初始规范 v0，M0 完成后以真实代码校正）

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口, PRAGMA 统一设置, 路由挂载
│   ├── config.py            # 环境变量/路径配置(数据卷路径集中于此)
│   ├── db.py                # SQLAlchemy engine/session, WAL/FK/busy_timeout PRAGMA
│   ├── models/              # SQLAlchemy ORM, 一表一文件
│   ├── schemas/             # Pydantic 请求/响应模型, 一域一文件
│   ├── api/                 # 路由层: auth.py, users.py, spaces.py, relations.py,
│   │                        #   graph.py, search.py, stats.py, attachments.py, admin.py
│   ├── services/            # 业务逻辑(路由层禁止直接写业务): visibility.py 唯一授权过滤点,
│   │                        #   relations_fsm.py, space_fsm.py, lunar.py, audit.py
│   └── utils/               # security.py(pin hash/JWT/challenge), pin_gen.py(secrets)
├── migrations/              # Alembic
├── tests/                   # pytest; test_authz_matrix.py 为 IDOR 矩阵测试家
└── pyproject.toml           # 锁版本
```

规则：
- 分层单向依赖 api → services → models；api 层只做参数校验与调用 service。
- 所有跨用户数据出口必须经 `services/visibility.py`，禁止在路由里内联可见性 if。
