# D 实施计划

仅规划；全部执行项待批准。串行依赖：C → D → E，D 先给 E 交付请求关联合同。

- [ ] 批准后通过 task.py start 建本任务分支/worktree；读取 backend/frontend 规范及 agent 历史压缩合同。
- [ ] 枚举 `admin_agent_latency` 全部调用者、internal schema、queue lease、events append、worker/SDK hook 和 ProviderGateway 审计字段；记录字段缺口。
- [ ] 迁入四个审计反例（工具批量时间、单失败成功、耗尽、零事件分母）；补多轮/无正文/跨 attempt/压缩/窗口边界，先证红。
- [ ] 冻结 design 字段表与 source/version/null 规则，核对 E 消费与生产责任；如需迁移串行确认唯一 head。
- [ ] 增最小源计时/请求关联并修聚合；不改变公开消息正文，不把诊断字段从内部泄漏给家庭接口。
- [ ] 检查 old-client/new-server 和 new-client/old-server 发布顺序；实际 internal 联调验证，不只 mock。
- [ ] 定向后跑受影响包门禁、管理员授权矩阵、API smoke；浏览器阶段交 F，不因此豁免。
- [ ] 更新 agent-runtime 可观测性条款、父研究 summary/HANDOFF，明确修正旧分段结论。
- [ ] 提交/串行集成；将确定版本交 E/F/G。归档后仅清理已合并且干净的分支/worktree。

## 验证入口（执行时先核对脚本）

```bash
cd backend
.venv/bin/pytest -q tests/test_admin_agent_latency.py tests/test_agent_events.py tests/test_provider_proxy.py tests/test_system_admin_boundary.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest -q
cd ../agent
npm run lint
npm run type-check
npx vitest run test/events.test.ts test/worker.integration.test.ts test/session-history.test.ts test/assistant-delta-gap.test.ts
npm test
npm run build
```

若管理员 decoder 或前端 API 有改动，运行对应包 lint/type-check/test/build。涉及 API 时由仓库根运行 `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-timing-smoke.json`，退出2为blocked；F 记录其真正连接的环境。迁移用显式临时 DATA_DIR 执行 Alembic，不依赖 DATABASE_URL 环境变量。

证据必须包括旧/新语义对照、每场景有效样本与缺失数、延迟注入点、容差、SDK/源码版本、隐私哨兵检查。不因测试通过宣称用户变快。
