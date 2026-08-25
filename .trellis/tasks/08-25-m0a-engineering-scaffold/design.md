# m0a 技术设计

> 遵守 [architecture.md](../../spec/architecture.md) §5（DB 契约）。本任务是全项目分层骨架的定义者。

## 边界与契约

- **分层单向依赖**：`api → services → models`，违规由 code review 把关（v1 不上 import-linter，M4 后评估）。
- **db.py 启动序列**：create engine → 事件钩子 `PRAGMA foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL`。WAL 文件与主库同卷。
- **Compose 拓扑**：
```
web(nginx:1.27-alpine) ──反代 /api──> api(python:3.12-slim, uvicorn)
   │静态: /usr/share/nginx/html (frontend build 产物, 多阶段构建拷入)
volume data: /data/db/app.db + /data/uploads + /data/backups
```
- nginx **不**直挂 uploads 目录（附件下载走授权端点，m2a 落地；此约束在 m0a 就写死配置防返工）。
- 配置集中 config.py：DATA_DIR、SECRET_KEY(env)、TOKEN_TTL 常量；SECRET_KEY 缺失时拒绝启动。

## 数据流

前端 dev 模式 vite proxy→uvicorn；生产模式 nginx 静态+/api 反代——两条路径行为一致性用 health 端点双向验证。

## 权衡

- 不引 CI 平台：门禁脚本本地跑，几十人项目收益不足（审计接受）。
- Alembic 从空库起步而非 SQL 初始化脚本：单一 schema 来源。

## 回滚形态

骨架 PR 独立合入 main；任何后续问题 revert 该 PR 不影响规划工件。
