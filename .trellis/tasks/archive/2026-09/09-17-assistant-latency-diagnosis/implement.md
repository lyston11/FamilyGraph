# A 实施计划（待批准，未执行）

## 0. 前置

- [ ] 用户批准执行后再 start；本轮 planning，不使用子智能体。
- [ ] 父任务共同 baseline 先冻结；可以先做本任务只读测量，实质修复排在 B 后，避免共享 backend/Provider/DB 并行。
- [ ] 任务 worktree 隔离；重新核对 HEAD、HANDOFF、Agent 设计、相关 Spec 和 SDK 实际版本。
- [ ] 核对当前 CCSwitch/Pi、数据库 Provider 解析与进程配置；敏感字段不输出，不更换模型来源。

## 1. 基线与盲区

- [ ] 从既有 admin latency/run/event/审计取得可用时间与状态，列数据缺失，不倒推 lease/TTFT。
- [ ] 确认 lease→context→session→prompt→工具轮次→event→settle 的所有调用点和错误路径。
- [ ] 同配置受控场景：无工具短答；一个只读工具；多轮工具；长历史触发 SDK 压缩；worker 排队；Provider 失败/重试；取消/失租。
- [ ] 区分上游 first_byte、first_text、message_end 与用户首次可见；不得记录 thinking/正文。
- [ ] 若需字段补充，先定 owner/schema/版本/兼容/隐私，再实现最小观测，历史字段允许 unknown。

## 2. 可重复探针与选择

- [ ] 实际 SDK fake stream 延迟 text_delta 与 message_end，确认公共事件边界与空最终回答保护。
- [ ] 本地 HTTP 经过实际 ProviderGateway 验证流及时性与取消；sidecar 不直接绕过网关。
- [ ] 事件持久化/SSE/渲染时序测试，工具 turn 不提前结束 pending，重连无重复。
- [ ] 关联重试/压缩/工具轮次，找到主导阶段；没有证据的原因保持假设。
- [ ] 形成最小修复方案与量化目标；若是逐字显示/模型参数/并发等范围变化，先更新规划让用户评审，禁止直接实现。
- [ ] 对已授权且证实的程序性修复做红→绿与前后对照；没有收益不宣称提速。

## 3. 验证命令（执行时按实际改动裁剪）

```bash
cd agent
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

cd ../backend
.venv/bin/pytest -q tests/test_admin_agent_latency.py tests/test_agent_events.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
```

- [ ] Provider/internal/lease 变更前用实际文件列表补齐对应 pytest；观察字段跨 schema 时两端 decoder 共验。
- [ ] API/认证/事件合同变更必须 `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`，blocked 不算通过。
- [ ] 浏览器真实接口验证排队、工具、正文、完成/失败、取消和重连；route mock 只能附作 UI 合同证据。
- [ ] 安全哨兵覆盖 key/token/prompt/thinking/工具返回不会入日志，自动压缩和完整历史不退化。

## 4. 真实对照与收尾

- [ ] 真实小样本先明确次数/token 上限、合成输入与 Provider 授权；生产只读观测优先，不重复发送私密历史。
- [ ] baseline/after 同配置，报告每个样本和失败，不以删去慢样本制造收益。
- [ ] 明确程序改进、展示收益与剩余上游时间；若无程序瓶颈，交付数据和选项并保持实际提速项未完成。
- [ ] 与 B 最終结果一起更新父 AC，不混淆助手 run 与管家 batch 租约。
- [ ] Spec/HANDOFF 和证据同步，自审后 commit/push，串行集成与部署验证；必需项通过后 archive 并清理隔离 worktree。

## 本轮执行记录（2026-09-17，已完成部分，提交 `076d631`）

本节取代上方清单中已兑现的条目；未列出的条目**仍未执行**。

已完成：

- §0：用户已批准执行、已 `start`、已用任务 worktree、未使用子智能体；重读了 Agent 设计、
  `agent-runtime.md`、`assistant-history-restoration.md` 与相关 Spec；B 已完成后才推进本任务。
- §1（1）：从既有 run/event 取得可用时间与状态并列出缺失。**关键更正**：
  `agent_run_events.created_at` + `run.started`（= lease→running 落库点，
  `agent_events._promote_to_running`）已足够分段；原 `admin_agent_latency` docstring
  的「无法分段、应补 FSM 生命周期事件」结论与生产数据不符，已改写。未用
  `lease_expires_at` 倒推任何时刻。
- §1（5）：确认**不需要**新增字段/schema/迁移（历史字段缺失即 `n=0`/`null`）。
- §2（5）部分：形成结论——样本指向**上游模型生成为主导**，非程序性排队/工具/落库。
- §3 部分：`tests/test_admin_agent_latency.py`（含新增分段与空样本用例）、`ruff check`、
  `ruff format --check`、`mypy app` 全绿；全量 pytest `1647 passed, 3 skipped`。
  未跑 agent/frontend 套件——本轮未改这两端。
- 真实只读样本：服务器隔离副本（`DATA_DIR` 显式指向并断言非生产路径）：
  `model_turn` 逐轮 p50 33.15s、`queue_wait` p50 0.94s、`tool_call` 0ms、`settle` max 10ms。
  证据：父任务 `research/evidence/assistant-phase-decomposition-2026-09-17.md`。

仍未执行（如实保留）：

- §1（2）：未穷尽 context/session/prompt 的全部错误路径（只追了事件管线与 lease→running 落库点）。
- §1（3）：**未做**受控场景矩阵（无工具短答/只读工具/多轮工具/SDK 压缩/排队/
  Provider 失败重试/取消失租）。
- §1（4）：未做受控延迟注入，故「上游流首正文 vs 最终 `message_end`」「公共事件 vs
  浏览器可见」的差值仍属**未实测**（A-03 的另一半）。
- §2（1）–（4）：未写 fake stream 探针、未跑本地 HTTP 经 ProviderGateway、未做
  事件/SSE/渲染时序测试。
- §2（6）：未做真实模型对照实验（生产仅 2 个 run，样本已用尽；不重发私密历史）。
- §3：未跑 `frontend-api-smoke.sh`（未改 API 合同）；未做浏览器真实链路验证。
- §4：未做真实小样本对照、未部署、未与 B 合并更新父 AC（见下）。

## 结论边界（不得混淆）

- **「延迟诊断已完成」成立**：分段可复核，且样本指向上游模型生成。
- **「慢响应已解决」不成立**：未做任何提速改动，父任务须保留该未解决项。
- **未实施的选项**（需用户显式选择，见 `design.md` §5 与证据文档 §5）：
  逐字/增量显示（delta 合同）、更快模型或更低推理档位、并发/预算扩容。

---

本轮没有运行上述命令或模型实验，只有规划验证。
