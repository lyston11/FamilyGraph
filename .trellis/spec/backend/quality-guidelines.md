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
