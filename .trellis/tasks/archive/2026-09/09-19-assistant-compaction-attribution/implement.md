# D2 实施计划

主会话内联，无子智能体。前置：F 的 A3 证据已固定（`main@802925f` 基线）。本任务与 E（取消语义）不共享文件，但按串行规则一次只集成一个分支。

## 顺序

- [ ] `task.py start` 建 worktree；同步 `origin/main`（须含 E 的取消修复，避免 A3 复验时基线漂移）。
- [ ] sidecar：删除 `EventTiming.compaction_ms`；把 `turnCompactionMs` 改为 run 级 `compactionMs`（只累计 `agent_start` 之后、`agent_settled` 之前的跨度）。
- [ ] sidecar：`EVENT_TYPES` 增 `run.compacted`；`mapSessionEvent` 不映射它（有状态，由 buffer 在 `agent_settled` 时产出），确认它与 `message_update` 一样不打断 prose 聚合。
- [ ] backend：`agent_events.EVENT_TYPES` 增 `run.compacted`；`_SIDECAR_TIMED_EVENT_TYPES` 增它；`EventTimingIn` 删 `compaction_ms` 及其校验分支。
- [ ] frontend：`types/agent.ts` 的 `AGENT_EVENT_TYPES` 增 `run.compacted`（store 默认分支忽略即可，不加渲染）。
- [ ] backend 聚合：`assistant_phases.compaction` 改从 `run.compacted` 取逐 run 样本；删 `_timing_compaction_ms`；更新 `ASSISTANT_PHASES_NOTES` 文案。
- [ ] 回归（真实 SDK 顺序，D2-R5）：
  - agent：新增对真实 SDK 广播顺序的断言（压缩在 turn 外）——把现有合成顺序用例改为断言「轮内压缩不计入正文 timing」+ 新增 `run.compacted` 聚合用例（含无压缩不发、跨 turn 不串、与 prose 顺序）。
  - backend：`run.compacted` 可带 timing、`compaction_ms` 字段不再被接受、settle 不含压缩跨度。
  - frontend：事件类型表与后端一致（若已有 parity 测试则扩展，否则不加新框架）。
- [ ] F 复验：A3-1/A3-4 通过；`settle` 不再包含压缩跨度；A1/A2/A4/A5/A6 与 UI 组回归无变化。
- [ ] 门禁：`cd backend && ruff check . && ruff format --check . && mypy app && pytest`；`cd agent && npx tsc --noEmit && npm run lint && npx vitest run`；`cd frontend && npm run lint && npm run type-check && npm test && npm run build`。
- [ ] 更新 spec（`agent-runtime.md` 的源计时合同 + `app/schemas/agent.py` docstring）：压缩是 run 级阶段、`model_turn` 不含压缩、prompt 前压缩计入 `prepare` 的取舍。
- [ ] 提交、串行集成 main、F 全量累计复验并更新 `evidence/matrix.md`。

## 最小验证入口

```bash
cd agent && npx vitest run test/events.test.ts test/worker.integration.test.ts
cd ../backend && .venv/bin/pytest -q tests/test_agent_events.py tests/test_internal_agent_api.py tests/test_admin_agent_latency.py
cd ../frontend && npx vitest run src/stores/__tests__/agent.spec.ts
# 端到端（F 的 harness，零模型费用）
python3 scripts/smoke/run_controlled_acceptance.py --scenario A3-compaction --no-reused-suites --report /tmp/f-a3-d2.json
```

## 风险与回退

- 新增公共事件类型：若前端类型表漏同步，`npm run type-check` 会报错（不是静默漂移）；回退只需删三侧注册。
- 聚合口径从逐 turn 改为逐 run：`PhaseStats` 形状不变，历史行无 `run.compacted` → `n=0`，不伪造样本。
- 若 F 的 A3-2/A3-3 因事件新增而回归（不应发生，`run.compacted` 非终态），先检查 seq 顺序是否被 prose flush 打乱。
