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

## Common Mistakes（M0 真实代码校正，2026-08-25）

- `ruff format` 与 `ruff check` 是两道独立门禁——新文件必须同时过 format --check（M0b 曾漏掉 11 个文件）。
- 防时序枚举的 dummy bcrypt 校验：假哈希 cost 必须与 `config.BCRYPT_ROUNDS` 同源，硬编码 rounds 会产生可测时序差。
- `DATABASE_URL` 由 `DATA_DIR` 派生而非独立环境变量；临时库验证请设 `DATA_DIR`。
- 结构化日志 user_id 经 `logctx` 注入，认证成功后必须回填，否则恒为 null。
