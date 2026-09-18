# D 实施计划

状态（2026-09-17）：**已实现、已验、待集成**。见 [evidence.md](evidence.md)。
串行依赖：C → D → E，D 先给 E 交付请求关联合同。

- [x] 通过 task.py start 建本任务分支/worktree；读取 backend/frontend 规范及 agent 历史压缩合同。
- [x] 枚举 `admin_agent_latency` 全部调用者、internal schema、queue lease、events append、worker/SDK hook 和 ProviderGateway 审计字段；记录字段缺口。
- [x] 迁入四个审计反例（工具批量时间、单失败成功、耗尽、零事件分母）；补多轮/无正文/跨 attempt/压缩/窗口边界，先证红。
- [x] 冻结 design 字段表与 source/version/null 规则，核对 E 消费与生产责任；迁移 0051 确认唯一 head。
- [x] 增最小源计时/请求关联并修聚合；不改变公开消息正文，不把诊断字段从内部泄漏给家庭接口。
- [x] 检查 old-client/new-server 和 new-client/old-server 发布顺序；实际 internal 联调验证，不只 mock。
- [x] 定向后跑受影响包门禁、管理员授权矩阵；API smoke 与浏览器阶段交 F，不因此豁免。
- [x] 更新 agent-runtime 可观测性条款，明确修正旧分段结论。
- [ ] 提交/串行集成；将确定版本交 E/F/G。归档后仅清理已合并且干净的分支/worktree。

## 已落实的关键点

- 分母来自 run 表：`runs_without_events` / `runs_without_first_lease` / `runs_without_start` 单列。
- 每阶段带 `basis`/`native_n`/`derived_n`，新旧样本不混成同一精度分布。
- `queue_wait` 用不可变 `first_leased_at`；`prepare` 单列，不归入排队。
- `compaction` 作为 `model_turn` 子成分单列；`provider_retry` 区分段数/单次失败/尾部耗尽。
- 迁移两列 nullable + 拒绝式降级 + 父级 preflight 先于 DDL（反例验证过）。
- 未做：部署、真实模型、浏览器测量（属 F/G）。

## 验证入口（已运行，结果见 evidence）

```bash
cd backend
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
.venv/bin/pytest -q
cd ../agent
npm run lint && npm run type-check && npm test && npm run build
```

涉及 API 时由仓库根运行 `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-timing-smoke.json`，
退出 2 为 blocked；F 记录其真正连接的环境。迁移用显式临时 DATA_DIR 执行 Alembic，
不依赖 DATABASE_URL 环境变量。

证据必须包括旧/新语义对照、每场景有效样本与缺失数、延迟注入点、容差、SDK/源码版本、
隐私哨兵检查。不因测试通过宣称用户变快。

