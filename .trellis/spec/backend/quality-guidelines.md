# 后端质量门禁（初始规范 v0）

提交前必须全绿：

```bash
cd backend
ruff check .            # lint
ruff format --check .   # 格式
mypy app/               # 类型检查(strict 渐进: 新文件必须 strict)
pytest                  # 单测+集成, 授权矩阵 IDOR 测试必须存在且通过
```

- 禁止模式：路由层内联业务逻辑/可见性判断；裸 except；print 调试；SQL 字符串拼接；同步阻塞调用混入 async 路由（lunar 换算等 CPU 密集放 threadpool）。
- 新增对外接口必须同时新增：Pydantic schema、可见性判定走 visibility.py 的证明（code review 检查项）、对应测试。
- 安全相关改动（auth/visibility/attachments）必须更新授权矩阵测试后再合并。
- 测试门槛：services 层行覆盖 ≥80%；FSM 与安全模块要求分支覆盖。

## V2.5 Memory/RAG/Policy Guard 验收检查（2026-08-26）

- 所有 Memory/RAG 写入必须走显式 service/domain command；普通 AgentMessage、私人 Session 和 Steward checkpoint 不得自动进入 RAG。候选 source message 必须校验所属 account，shared scope 必须校验 active membership 和目标空间。
- 检索必须先执行 actor/space/scope/visibility/confirmation/sensitivity/status 过滤，再进行 FTS 或可选 embedding；撤销、过期、删除和 Profile 删除必须同步 tombstone 文档与 chunks。
- ContextBuilder 的 provider 决策和 `ContextBuild` 追踪必须使用同一 scope 过滤结果；Pi `context` hook 只处理 Run context endpoint 已预取的数据，禁止在 hook 内查库。
- Policy Guard 对 masked、跨 scope、未确认事实和敏感 cloud provider 必须 fail-closed；未知/畸形 hook payload 不能当作允许。测试需验证工具结果、模型输出、provider request 和持久化前后四类边界。
- BehaviorProjection 只能由 append-only DomainEvent 重放重建；不得用键鼠/停留时长等泛行为。重建不得在普通 job 中清掉刚写入的合法冷却/偏好，除非显式执行 rebuild。

### V2.5 可执行门禁

```bash
cd backend && .venv/bin/python -m pytest -q && .venv/bin/python -m mypy app
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Common Mistakes（M0 真实代码校正，2026-08-25）

- `ruff format` 与 `ruff check` 是两道独立门禁——新文件必须同时过 format --check（M0b 曾漏掉 11 个文件）。
- 防时序枚举的 dummy bcrypt 校验：假哈希 cost 必须与 `config.BCRYPT_ROUNDS` 同源，硬编码 rounds 会产生可测时序差。
- `DATABASE_URL` 由 `DATA_DIR` 派生而非独立环境变量；临时库验证请设 `DATA_DIR`。
- 结构化日志 user_id 经 `logctx` 注入，认证成功后必须回填，否则恒为 null。

## 系统管理员治理边界回归（09-01 system-admin-governance-routes 沉淀）

后台治理路由（system_admin/admin_metadata 等）的安全回归测试必须同时覆盖四类断言，参考 `tests/test_system_admin_boundary.py`：

- **主体互斥矩阵**：system-admin token 通过；family_user token / 无 token / 错误或未知 `principal_type` 一律 403/401；反向隔离——`/api/me`、`/api/spaces` 等家庭端点拒绝 system_admin 与伪造 token。
- **字段白名单用精确集合断言**：治理响应 schema 逐字段断言允许集合（不是「包含」断言），确保不泄露 birth/gender/bio/avatar/附件/关系图边/私人 Memory/Session/disclosure/pin_hash。新增治理字段必须同步更新白名单测试。
- **防存在性枚举**：未知 space_id 与真实 space_id 的成员查询必须返回不可区分的安全空结果，而不是 404。
- **旧 break-glass 路由不回归**：`backend/app/api/admin.py`（家庭 PIN 重置/资料修改/custody transfer/claim dispute/data-rights）保持不注册；`test_legacy_break_glass_admin_routes_are_not_registered` 用 FastAPI app route registration 断言这些路径不存在。家庭 break-glass 迁移必须另立任务，禁止为「补齐后台」挂载旧 router。

## 独立系统管理员认证与 Listener 隔离回归（09-04-system-admin-auth-api-isolation 沉淀）

系统管理员后台已迁移到独立 `admin_app`（8002，`/admin-api/*`，用户名+密码认证），上述四类断言继续适用于 admin 路由面，并新增以下必测项（参考 `tests/test_system_admin_boundary.py`、`tests/test_bootstrap_api.py`）：

- **双 listener 路由注册断言**：family app 无 admin 业务路由、OpenAPI 无后台路径；admin app 恰好只有 `/admin-api/auth/*` 七条；旧 `admin.py` 的 break-glass 路径在两个 listener 均不存在。
- **普通 404 一致性**：`/admin-api/*` 在 8000 的响应必须与随机未知路径逐字节一致（同 catch-all 处理器）；不允许 403/重定向/自定义错误页。
- **交叉签发域拒绝**：family token→8002 与 admin token→8000 双向 401；admin JWT 依赖独立 `ADMIN_JWT_SECRET/ISSUER/AUDIENCE`，claim 版本字段与家庭刻意不同名（`token_version` vs 家庭 `ver`）。
- **凭据文件生命周期**：空库 bootstrap 生成唯一 `admin`、`DATA_DIR/bootstrap/admin-credentials` 权限 0600、日志 grep 无明文、首登改密后删除、删除失败回置 `password_must_change` 并审计；lifespan→preflight 接线有测试。
- **初始/恢复密码来源**：`ADMIN_INITIAL_PASSWORD` 由部署环境提供，bootstrap 与 `app.admin_recovery` 共用；未配置/空值沿用随机密码。配置值只允许非空白单行、无 NUL、UTF-8 不超过 bcrypt 的 72 字节上限。值不进入源码、日志、审计或 API 响应。初始密码可短于常规改密下限，但仍强制首次改密，常规改密强度校验不变；重启不覆盖已有账号的密码。回归覆盖配置值/随机回退、错误配置拒绝、首次改密门禁和旧会话撤销。
- **会话撤销触发器矩阵**：改密/改用户名/锁定/运维恢复每个分支都断言 `password_version+1` 且 refresh session 全撤销；refresh 轮换断言新行 `expires_at` 与原会话一致（绝对有效期、轮换不续期）。
