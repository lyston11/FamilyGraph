# 系统管理员密码认证与独立 Admin API Listener：实施计划

## 1. Preconditions

- [ ] 读取父任务 PRD/design/notes/research 与 backend auth/database/error-handling 规范。
- [ ] 检查当前 `main.py`、`serve.py`、`auth.py`、`deps.py`、system_admin models/migrations 和测试。
- [ ] 确认 `backend/app/api/admin.py` 未注册；保留其他并行工作树修改。

## 2. Ordered implementation

- [ ] 建立 admin credential schema/service：username、password_hash、password_version、锁定、恢复。
- [ ] 增加 Alembic revision；对旧 PIN 结构不做兼容迁移，无法识别的旧状态 fail-closed。
- [ ] 拆分 `family_app` 与 `admin_app` 的认证 router 和 response schema。
- [ ] 为 `serve.py` 增加 8002 admin listener，并加入 host/port/secret/issuer/audience fail-closed 预检。
- [ ] 实现部署自动 bootstrap、0600 凭据文件、首次改密删除和受限恢复 CLI。
- [ ] 实现 admin JWT 签发/验证、短 access、refresh 轮换、绝对有效期和版本失效。
- [ ] 从 8000 移除 system-admin/bootstrap/admin metadata/system-admin application routes；为后台路径返回普通 404。
- [ ] 从家庭 auth response/types 删除后台身份枚举，保留家庭登录合同。
- [ ] 补齐认证、路由、跨 listener、锁定、文件权限和家庭回归测试。

## 3. Verification

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_admin_auth_listener.py tests/test_admin_auth_lifecycle.py tests/test_family_auth_regression.py
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/python -m mypy app
```

运行服务/Compose 验证：

```bash
curl -i http://127.0.0.1:8000/admin-api/auth/login  # 普通404
curl -i http://127.0.0.1:8002/admin-api/health
```

## 4. Stop points

- 不能把 admin_app 与 family_app 的 issuer/audience/secret 分开时停止。
- 8000 仍出现 admin 路由、后台 OpenAPI 或 system_admin 字段时停止。
- 需要把旧 PIN 当密码、固定 admin/admin 或共享认证端点才能工作时停止。
- bootstrap 密码进入普通日志、DB 明文、前端或文件权限不为 0600 时停止。
- 任何实现尝试注册旧 `admin.py` 时停止。

## 5. Rollback

- 可以回滚 admin listener 和 admin schema，但不要把后台路由重新塞回家庭 app。
- 若 admin listener 失败，家庭 8000 继续提供家庭服务；部署必须报警，不得降级代理到 8000。
- 保留审计/凭据 schema migration 记录，不删除已产生的安全审计。

## 6. Handoff

完成后输出：新增 migration、admin_app/router、credential service、配置键、恢复命令、测试结果、8000/8002 路由表和旧 admin.py 未注册证据，供子任务 2/3 使用。
