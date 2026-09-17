# C 实施计划

状态：仅规划，以下均未执行。父任务已授权的旧实现不等于本修订获准。

## 顺序与关口

- [ ] 审阅本任务 PRD/design、父任务最新 audit summary；批准后在主检出 `task.py start`，读取 task.json 并进入新建 worktree。主会话内联处理。
- [ ] 重读完整 `steward-action-card.md`（不可接受 32768 字节截断）及 runtime shutdown/所有 transport 调用者。
- [ ] 将历史 400ms→638ms 反例转成仓库正式回归；补响应头、body stall、慢 chunk、发送前锁等待，先在旧实现证红。
- [ ] 验证可中断 transport 及清理边界，记录同步解析的处理上限；技术门不通过先更新设计。
- [ ] 实现固定截止、结算预留、出事务再核对；保持逐笔保存/恢复逻辑，补取消接管与未知计费断言。
- [ ] 连续重复超时/停止检查资源数量，测试使用临时 DATA_DIR，不访问运行中的生产库。
- [ ] 运行定向检查，再运行 backend 门禁；受影响的 runtime/API 测试按实际改动补齐。
- [ ] 更新 Spec 中错误的总截止声明、研究证据与父 AC-04；交 F 累计验收。本地通过不勾 G 部署项。
- [ ] 小步提交并备份分支；串行集成后 archive，满足已合并且干净条件才清理 worktree/分支。

## 验证入口

```bash
cd backend
.venv/bin/pytest -q tests/test_steward_assist.py tests/test_steward_terminology_delivery_integration.py tests/test_steward_terminology_runtime_quality.py tests/test_steward_runtime_recovery.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest -q
```

新 socket 测试归入现有测试文件或其最小合适拆分；不要把历史证据脚本直接变成生产测试依赖。确有迁移时先隔离 `alembic upgrade head`，不能只用 ORM create_all。

## 完成证据与回退点

记录源码 SHA、Python/HTTPX 版本、预算/实测/容差、重复次数、截止后活跃资源、attempt 与 batch 安全状态。结果不含正文或密钥。所有 C-AC 必须逐条有证据，失败不得转 skipped；已知机器争用 flaky 单列，不能用扩大业务 timeout 消除。

回退前保存分支与测试证据；代码回退不 downgrade 业务事实、不清除 unknown。不自行部署，部署交 G。
