# 执行计划：双 Agent 延迟与结果保全

## 0. 执行状态（2026-09-17 更新）

规划已获用户批准并执行完毕。两个子任务均已实现、验证、归档与清理；集成验收见
`research/integration-acceptance.md`。父任务因 AC-01/AC-06/AC-07 的**真实提速与受控场景矩阵**未完成，
**不归档为 completed**（按 R8/AC-09）。下文复选框反映实际执行结果。

集成期又完成两项助手侧事实补测（只读 + 新增回归，未提速）：

- **A-02 落实**：`model_turn` 含上游 5xx 重试退避，故在 `assistant_phases` 中新增
  `provider_retry`（下界）/`provider_failed_attempts`（无歧义）/`runs_with_provider_retry`，
  使「重试」不再被当作单次模型推理。回归 `test_latency_metrics_separates_provider_retry_from_generation`。
- **实际推理档位**：真实 SDK 探针实测为 SDK 默认 `medium`（平台无档位控制项），已记入证据，未改动。

## 1. 任务树与依赖

- 父任务：`09-17-dual-agent-latency-result-integrity`，拥有共同基线和集成 AC。
- A：`09-17-assistant-latency-diagnosis`，助手测量及证实问题的最小修复。
- B：`09-17-steward-attempt-result-integrity`，管家结果保全、时限与恢复。
- 执行顺序：共同基线 → B 红测与修复 → A 针对实测瓶颈推进 → 共同复测/部署验收。单线程推进，共享 backend/provider/SQLite/端口时不并行。
- 如后续选择逐字展示/新模型/unknown 重试，先补齐相应 PRD 和设计重新评审，不夹带进当前代码变更。

## 2. 规划交付检查

- [x] 三个任务均有 PRD、design、implement；已 `task.py start` 并执行完毕。
- [x] Research index/summary/evidence 分层，源码与旧证据引用可定位。
- [x] JSONL 仅精确 Spec 叶与 summary；未派发子智能体（按用户要求主会话直接执行）。
- [x] 运行任务 validate；业务文件改动仅在子任务 worktree 内。
- [x] 向用户提交规划摘要后停止，等批准后才执行。

## 3. 共同基线

- [x] 重读 HANDOFF、最新任务/spec、相关源码与 git 状态，记录源码 commit（`62a2b15` 基线）。
- [x] 在子任务自动生成的 branch/worktree 开发；父任务未借规划授权启动实现。
- [x] 只读核对远端进程、配置、Provider 解析链与队列状态；敏感值未输出。
- [x] 收集既有延迟 API/安全审计与 run/batch 元数据；只读副本上取数，未污染生产库。
- [x] 定义双链路阶段表与口径红线（首字 vs 首完整消息、缺失即 null 不零填充）。
- [x] 隔离 DATA_DIR 时序探针；未导入 app 后猜库路径。
- [x] 字段充足性已核实：助手分段**无需新增字段**（既有 `agent_run_events.created_at` 足够）。
- [x] 改动前保存 baseline；真实小样本上限明确。**未做**：受控场景矩阵与全局 SLO。

## 4. B 实施门（已完成）

- [x] 红测先行：逐笔保全、混合 recovery、空结果、慢 chunk 四类均先证红。
- [x] 每笔调用先结算再发下一笔；保持 owner/attempt/deadline 栅栏及网络事务边界。
- [x] 混合成功与 unknown 的独立产物恢复；批次仍如实 `failed`，不伪装完全成功。
- [x] transport 总截止与收尾预算；慢 chunk 与资源回收验证。
- [x] 四 kind 回归、失租旧 worker 拒写、真实 unknown 禁止重放。
- [x] 修正 Spec 中的旧声明；未改写历史验收原始数据。

## 5. A 实施门（部分完成）

- [x] 助手分段实现并从既有持久事件取数；样本 n=2（生产仅 2 run，已用尽）。
- [x] 分清排队/模型生成/工具/落库/发布时机；每项给样本或标 unknown。
- [x] 已证实的问题（发布时机导致首段晚）给出双向 pin 的回归；**未做提速改动**。
- [x] 逐字显示未实施，合同要求已写入 spec 供后续评审。
- [x] 空最终回答、取消、失租、SSE 重连、完整历史压缩回归全绿。
- [ ] **未完成**：受控场景矩阵（无工具/只读工具/多轮/压缩/排队/失败重试/取消失租）。
- [ ] **未完成**：真实模型对照与提速验证（需用户先决定选项）。

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

- [x] 真实 Provider 测试沿用既有授权与配置，未改模型/档位/预算；未重发历史 unknown。
- [x] 通过正常链路观察（生产只读副本）；未手写模型投影制造 AC。
- [ ] **未完成**：before/after 真实对照（助手样本用尽，需用户先决定提速选项）。
- [x] AC 表逐项附证据；上游慢如实列出，必需项阻塞时不归档为 completed。
- [x] 更新相关 Spec/HANDOFF，补充旧归因纠正（“助手无法分段”说法已改写）。
- [x] 串行集成、受影响验证、push；未部署（无生产部署需求：零迁移、无 API 破坏性变更）。
- [x] 子任务验证通过后依次归档；父任务因必需 AC 未完成而**保留未完成**。

## 9. 实际执行记录（2026-09-17）

```text
B: e482f59 fix(steward): persist each assist result before the next request
   9c4a677 test(steward): cover legal empty terminology result as checked
   062e4e6 docs(task): record steward result-integrity evidence and execution log
   45f636a chore(task): archive 09-17-steward-attempt-result-integrity
A: 076d631 feat(admin): decompose assistant latency from persisted run events
   32b80f1 test(agent): measure the delta-to-visible gap against the real SDK
   729216a docs(task): record assistant latency diagnosis findings and spec contract
   358766b chore(task): archive 09-17-assistant-latency-diagnosis
集成: 651ee07 Merge branch 'feat/09-17-assistant-latency-diagnosis'
```

验证（集成后 `main`）：后端 `1648 passed / 3 skipped` + ruff/format/mypy 全绿；agent `127 passed`
+ lint/type-check/build 全绿；API smoke `56/56`。未跑浏览器端到端（未改前端）。

清理：两个子任务 worktree 与分支均已删除，主检出干净。

## 8. 交付证据结构

Research summary 只保存可复用结论和关键约束；`research/evidence/` 保存基线/复现/前后比较/安全矩阵。证据写安全时间与状态，不写完整 prompt/响应、凭据、用户隐私。执行前后分开，规划期不勾实施完成。
