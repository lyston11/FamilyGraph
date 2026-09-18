# 执行计划：双 Agent 延迟与结果保全

## 0. 执行状态（2026-09-17 更新）

原 A/B 已实现、验证、归档与清理，但父任务未完成。本轮在 `main@d8d3668` 复核发现：总截止与结算预留未闭合，助手持久事件时间不等于执行时间，重试/分母存在盲区，运行服务未加载修复且浏览器验收缺失。最新依据：[复核summary](research/review-summary.md)、[任务图](research/remediation-task-map.md)。

用户已批准创建最新 C～I 子任务及完整规划；**2026-09-18 更新：09-18 已完成并归档**（P0-1/P0-2/P1-1 已集成并部署，LL-AC4 真实浏览器验收通过），原 H 归档并入 09-18，E 工程部分已集成 main；F/G/I 仍 planning。** 父 AC-01/04/06/07/08/09 未闭合，保持 in_progress。AC-06 不强制真实提速或降档；要求可靠归因，做优化时再证明对应阶段改善。

下列原实施记录保留真实已做部分，过度完成项已重新打开。

集成期又完成两项助手侧事实补测（只读 + 新增回归，未提速）：

- **A-02 部分落实**：已新增 `provider_retry` / `provider_failed_attempts` / `runs_with_provider_retry`，但单失败、失败耗尽、审计遗漏与轮次关联仍有缺陷，不能宣称已准确分离。由 D/E 修复。
- **实际推理档位**：真实 SDK 探针实测为 SDK 默认 `medium`（平台无档位控制项），已记入证据，未改动。

## 1. 任务树与依赖

- 父任务：`09-17-dual-agent-latency-result-integrity`，拥有共同基线和集成 AC。
- A：`09-17-assistant-latency-diagnosis`，助手测量及证实问题的最小修复。
- B：`09-17-steward-attempt-result-integrity`，管家结果保全、时限与恢复。
- 执行顺序：共同基线 → B 红测与修复 → A 针对实测瓶颈推进 → 共同复测/部署验收。单线程推进，共享 backend/provider/SQLite/端口时不并行。
- 新一轮执行顺序：C → D → E → F → G；09-18（承接原 H 增量显示）按体验目标实施，I 为参数取舍决策任务，具体映射见任务图。共享模块/SQLite/端口始终串行。
- 如后续选择逐字展示/新模型/unknown 重试，先补齐相应 PRD 和设计重新评审，不夹带进当前代码变更；增量显示已由 09-18 承接并进入实施（见其 design「增量安全合同」）。

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
- [ ] D：重做字段充足性判断；created_at仅表示入库，不能独立给出准确工具/排队/压缩阶段。最小新增源计时须有schema与历史null规则。
- [x] 已保存旧 baseline；新真实小样本上限仅在 G 提案中列出，未批准。受控矩阵仍未完成，不承诺全局 SLO。

## 4. B 既有成果与重新打开项

- [x] 红测先行：逐笔保全、混合 recovery、空结果、慢 chunk 四类均先证红。
- [x] 每笔调用先结算再发下一笔；保持 owner/attempt/deadline 栅栏及网络事务边界。
- [x] 混合成功与 unknown 的独立产物恢复；批次仍如实 `failed`，不伪装完全成功。
- [ ] C：原慢chunk测试不足，headers/body阻塞已证红；实现可中断总截止、结算预留和资源回收。
- [x] 四 kind 回归、失租旧 worker 拒写、真实 unknown 禁止重放。
- [x] 修正 Spec 中的旧声明；未改写历史验收原始数据。

## 5. A 实施门（部分完成）

- [x] 助手分段实现并从既有持久事件取数；样本 n=2（生产仅 2 run，已用尽）。
- [ ] D/F：区分源执行计时与持久化间隔，补准备/纯工具轮/压缩/浏览器证据；旧指标不足以排除这些瓶颈。
- [x] 已证实的问题（发布时机导致首段晚）给出双向 pin 的回归；**未做提速改动**。
- [x] 逐字显示未实施，合同要求已写入 spec 供后续评审。
- [x] 空最终回答、取消、失租、SSE 重连、完整历史压缩回归全绿。
- [ ] **未完成**：受控场景矩阵（无工具/只读工具/多轮/压缩/排队/失败重试/取消失租）。
- [ ] G：当前版本真实合成验证；若实际做性能优化再提供同配置前后对照，不强制先降档或换模型。

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
- [ ] G：真实合成样本和当前版本验证待执行；不同版本/配置样本不可强行计算提速。
- [x] AC 表逐项附证据；上游慢如实列出，必需项阻塞时不归档为 completed。
- [ ] C/D/E：修正 Spec 的截止、计时和重试过度结论；本轮先更新父任务/HANDOFF的当前事实，不把待实现合同写成已生效。
- [x] 旧代码串行集成、验证、push；这些不证明生产加载。
- [ ] G：完成运行版本核对与批准后的发布；零迁移/兼容接口也需要加载新服务代码。
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
+ lint/type-check/build 全绿；API smoke `56/56`。未跑浏览器端到端；这是待补缺口，由F负责，不能因未改前端豁免。

清理：两个子任务 worktree 与分支均已删除，主检出干净。

## 8. 交付证据结构

Research summary 只保存可复用结论和关键约束；`research/evidence/` 保存基线/复现/前后比较/安全矩阵。证据写安全时间与状态，不写完整 prompt/响应、凭据、用户隐私。执行前后分开，规划期不勾实施完成。
