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
