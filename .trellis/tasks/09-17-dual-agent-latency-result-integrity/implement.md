# 执行计划：双 Agent 延迟与结果保全

## 0. 本轮停止点

只交付规划。以下执行项全部待办；不得 `task.py start`、建实现 worktree、写业务代码、运行生产压测或调参。用户审阅最新 PRD/design/implement 并另行批准后才进入执行。全过程不使用子智能体。

## 1. 任务树与依赖

- 父任务：`09-17-dual-agent-latency-result-integrity`，拥有共同基线和集成 AC。
- A：`09-17-assistant-latency-diagnosis`，助手测量及证实问题的最小修复。
- B：`09-17-steward-attempt-result-integrity`，管家结果保全、时限与恢复。
- 执行顺序：共同基线 → B 红测与修复 → A 针对实测瓶颈推进 → 共同复测/部署验收。单线程推进，共享 backend/provider/SQLite/端口时不并行。
- 如后续选择逐字展示/新模型/unknown 重试，先补齐相应 PRD 和设计重新评审，不夹带进当前代码变更。

## 2. 规划交付检查（本轮）

- [ ] 三个任务均有 PRD、design、implement，status=planning。
- [ ] Research index/summary/evidence 分层，源码与旧证据引用可定位。
- [ ] JSONL 仅精确 Spec 叶与 summary；不派发子智能体。
- [ ] 运行任务 validate 和本地文档引用/状态检查；业务文件零改动。
- [ ] 向用户提交规划摘要后停止。

## 3. 批准后的共同基线

- [ ] 重读 HANDOFF、最新任务/spec、相关源码与 git 状态，核对旧任务成果，记录源码 commit。
- [ ] 启动实际实现子任务，在其自动生成的 branch/worktree 开发；父任务不借规划授权启动实现。
- [ ] 只读核对远端进程、实际加载配置、Provider 解析链、队列状态；敏感值不输出，排查根因不依赖 health 单点。
- [ ] 收集既有延迟 API/安全审计与当前 run/batch 元数据。明确 API 读操作可能写访问审计；需要纯只读 DB 时使用只读连接。
- [ ] 按同配置定义助手与管家阶段表、采样窗口、缺失字段、失败分母；记录首字与首完整消息的区别。
- [ ] 在本地/隔离 DATA_DIR 做时序探针；不导入 app 后再猜库路径。
- [ ] 若已有字段不足，冻结最小字段/事件方案、兼容策略与迁移判断后再写实现。
- [ ] 在改动前保存 baseline；设定受控场景的比较项及真实小样本调用上限，不制造无依据的全局响应 SLO。

## 4. B 实施门

- [ ] 完成 B 设计的 S1–S10 故障矩阵红测，特别是第一笔成功/第二笔耗尽租约与混合 recovery。
- [ ] 每笔调用先审计后发下一笔；保持 owner/attempt/deadline 栅栏及网络事务边界。
- [ ] 混合成功与 unknown 的独立产物恢复；校验幂等、费用、批次状态聚合与旧行兼容。
- [ ] transport 总截止与收尾时间预留；慢 chunk 及资源回收验证，禁止后台遗留网络线程。
- [ ] 四 kind 回归、无权限/语义变化/旧 worker 拒写、真实 unknown 禁止重放。
- [ ] 核验 Spec 与历史声明需修正的位置，不改写历史验收当时的原始数据。

## 5. A 实施门

- [ ] 完成 A 阶段矩阵，验证代理 egress、sidecar context/SDK/工具与前端显示的时序关联。
- [ ] 分清 SDK/代理重试、queued、工具多轮、压缩、模型生成与消息缓冲；对每个结论给样本支持或 unknown。
- [ ] 对已证实且不改变产品合同的问题做最小修复与回归；若无程序性瓶颈，交付可审阅选择并保持“实际提速”未完成。
- [ ] 若提出逐字显示，先冻结跨层事件合同/引用与失败语义并让用户审阅，不仅修改 events.ts。
- [ ] 保持空最终回答失败、非空 length 回答、取消、失租、SSE 重连和完整历史压缩回归。

## 6. 验证命令（后续，不是本轮运行记录）

先读各包 package.json 确认命令，先定向后扩大。

```bash
cd backend
.venv/bin/pytest -q tests/test_steward_assist.py tests/test_steward_terminology_runtime_quality.py tests/test_steward_terminology_delivery_integration.py tests/test_admin_agent_latency.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app

cd ../agent
npm run lint
npm run type-check
npx vitest run test/events.test.ts test/session-history.test.ts test/worker.integration.test.ts
npm test
npm run build

cd ../frontend
npm run lint
npm run type-check
npx vitest run src/stores/__tests__/agent.spec.ts src/components/agent/__tests__/AgentPrimitives.spec.ts
npm test
npm run build
```

涉及 ProviderGateway/internal schema/SSE 的变更时补后端 `test_agent_events.py`、运行时/provider/internal 已有测试（用 `rg --files backend/tests` 选实际路径）及真实 internal 联调。涉及管理员延迟接口则补管理员边界测试；仅接口兼容字段而未改后台前端，也需核对 decoder。

涉及 API 合同需运行 `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`，退出 2 按 blocked，不得以 mock 浏览器代替。若迁移，先隔离 `DATA_DIR` 执行 Alembic upgrade 并验证旧行/拒绝边界；序号以执行时 head 为准。

浏览器：同一合成会话验证提交、queued、工具轮次、首正文、完成/失败、取消、断线恢复；记录用真实 API 还是 route mock，mock 只证明界面合同。

## 7. 真实环境与交付

- [ ] 真实 Provider 测试前确定小样本上限、输入脱敏、隔离目录与授权，保持模型/档位/预算不变；不自动重发历史 unknown。
- [ ] 通过正常链路观察 core/assist 分别完成，不能手写模型投影制造 AC。
- [ ] 记录 before/after 全体样本及失败/删失，不把超时升高后的更慢成功当提速。
- [ ] AC 表逐项附证据；上游慢/没有合法改善仍如实列出，必需项阻塞时不归档为 completed。
- [ ] 更新相关 Spec/HANDOFF，补充旧归因纠正；无关旧文档错误不顺手整修。
- [ ] 串行集成、受影响验证、push；部署另核对 commit/迁移/服务及实际功能，不用 health 代替。
- [ ] 子任务验证通过后依次归档；父任务全部 AC 通过后归档。删除前确认分支已合 main、worktree 无未提交代码，禁止强制清理。

## 8. 交付证据结构

Research summary 只保存可复用结论和关键约束；`research/evidence/` 保存基线/复现/前后比较/安全矩阵。证据写安全时间与状态，不写完整 prompt/响应、凭据、用户隐私。执行前后分开，规划期不勾实施完成。
