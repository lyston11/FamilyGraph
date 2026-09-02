# Agent Runtime assistant-only 收口：实施计划

## Preconditions

- [ ] 用户审阅并显式批准最新 PRD/design/implement 总结。
- [ ] `python3 ./.trellis/scripts/task.py validate 09-01-agent-runtime-assistant-only` 通过。
- [ ] `implement.jsonl`、`check.jsonl` 已包含真实 spec/research 条目。
- [ ] 执行前用 `task.py start 09-01-agent-runtime-assistant-only` 将状态置为 `in_progress`。
- [ ] 记录起始 `git status --short`；保留人物去重及其他任务的 WIP，不格式化或提交无关文件。

## Phase 1 — Reconcile WIP with the approved contract

- [ ] 审查当前未提交 diff，按本任务/人物去重/其他任务标记文件和 hunk 归属。
- [ ] 比较 `4f73146..working-tree` 的 Agent Runtime 变更，确认没有依赖未提交的人物去重代码。
- [ ] 建立任务 owned-files/hunks 清单；发现交叉依赖时先回到设计，不直接把无关 WIP 纳入。
- [ ] 确认迁移前序仍为 `0023_system_admin_decision_ref`，数据库测试使用临时 `DATA_DIR`。

Rollback point：尚未增加新代码；可恢复到进入任务时的共享 WIP 状态，不删除任何用户改动。

## Phase 2 — Split runtime and policy consumer kinds

- [ ] 将 generic Runtime 类型命名为 `RuntimeAgentKind`/`RUNTIME_AGENT_KINDS`（或项目内同等清晰名称），只允许 Assistant。
- [ ] 增加独立 `PolicyConsumerKind`/`POLICY_CONSUMER_KINDS`，允许 Assistant 和 Steward；放置在不把 consumer 层反向耦合到 generic table 模型的位置。
- [ ] 更新 models/schema/queue/token/tools/internal API，使其只使用 runtime kind。
- [ ] 更新 ContextBuilder/RAG policy seam，使其使用 policy consumer kind。
- [ ] 保持 Steward consumer shared-only；恢复/补充 private、other-space、public unrestricted 拒绝测试。
- [ ] 证明 ContextBuilder 的 `run_id=None` Steward policy seam 不创建伪 `ContextBuild`/`AgentRun`。

Targeted checks：

```bash
cd backend && ../.venv/bin/python -m pytest -q \
  tests/test_memory_rag_service.py \
  tests/test_agent_schema_contract.py \
  tests/test_steward.py
cd backend && ../.venv/bin/python -m mypy app
```

Rollback point：若分层导致循环依赖或 ContextBuild 合同不成立，保留 runtime assistant-only，回退 consumer 常量放置方案并更新 design；不得删除 Steward policy tests。

## Phase 3 — Tighten generic backend protocol

- [ ] `LeaseRequest.kind` 改为无默认值的 required Assistant literal。
- [ ] `lease_next` 服务层不再接受 `None` 隐式选择 Assistant。
- [ ] queue、token、internal route 在读写/签发前统一 fail-closed。
- [ ] 移除 generic Steward concurrency/index/error 生产语义；只保留历史 migration/downgrade 需要的文本。
- [ ] 保留 Assistant session/account 并发、cancel、reaper、event 和 idempotency 不变式。
- [ ] 增加省略 kind、`steward`、unknown、null/畸形输入的回归；断言不产生 Run/Job/token/Provider 副作用。

Targeted checks：

```bash
cd backend && ../.venv/bin/python -m pytest -q \
  tests/test_agent_queue.py \
  tests/test_agent_tokens.py \
  tests/test_internal_agent_api.py \
  tests/test_agent_browser_api.py \
  tests/test_agent_schema_contract.py
```

Rollback point：协议改动保持后端 schema 为权威；若 sidecar 尚未同步，不恢复默认值，先完成下一阶段双侧改动。

## Phase 4 — Tool and sidecar convergence

- [ ] 后端 registry 和 Node declarations 删除 `familygraph.steward_ping`。
- [ ] `required_kind` 两侧名称和语义一致；旧 `min_kind` 无生产引用。
- [ ] 保留 `record_term_usage`，核验 consent、run scope、allowlist、audit 和 tool-call idempotency。
- [ ] 修正 sidecar/client/prompt/schema 注释中“全部只读”“尚无去重表”等过期文字。
- [ ] sidecar 严格拒绝非 Assistant lease/context；测试断言拒绝前不会构造 session、发 Provider 请求或 dispatch 工具。
- [ ] 保持合法 Assistant lease/context/tool/event/settle 流程。

Targeted checks：

```bash
cd backend && ../.venv/bin/python -m pytest -q \
  tests/test_agent_tools.py \
  tests/test_agent_query_tools.py \
  tests/test_internal_agent_api.py
cd agent && npm test -- --run \
  test/client.test.ts \
  test/tools.test.ts \
  test/worker.integration.test.ts
cd agent && npm run type-check && npm run lint && npm run build
```

Rollback point：后端 schema 与 sidecar 必须一起收敛；不得靠宽松 decode 或默认 kind 暂时兼容。

## Phase 5 — Migration 0024 hardening

- [ ] 复核手写 SQLite DDL 与当前 `0009..0023` 最终 schema 的列、default、FK、index、trigger 完全一致，仅 kind CHECK/index 发生预期变化。
- [ ] preflight 在 DDL 前拒绝 session/run/job 中 steward、unknown、null，并输出表/id/kind。
- [ ] 空库 upgrade 到 head。
- [ ] 合法 Assistant 数据 upgrade，核验 message/event/tool-call、run↔job 双向关系、runtime snapshot、consent 和 scope immutable trigger。
- [ ] 冲突数据 upgrade 失败后核验原表、行、版本和 FK 完好。
- [ ] downgrade 恢复历史 kind 可表达性和 Steward partial index；再 upgrade round-trip。
- [ ] 所有迁移命令使用临时 `DATA_DIR`，执行 `PRAGMA foreign_key_check`。

Targeted checks（以仓库实际 migration test 名称为准）：

```bash
cd backend && ../.venv/bin/python -m pytest -q \
  tests/test_db.py \
  tests/test_agent_queue.py
```

必要时增加独立 `test_agent_runtime_assistant_only_migration.py`，避免把大量迁移 SQL 用例塞进 queue 测试。

Rollback point：迁移发现 schema 漂移时停止，不运行开发库 upgrade；修复手写 DDL/测试后从临时目录重跑。

## Phase 6 — Documentation and scope audit

- [ ] 更新 `.trellis/spec/backend/agent-runtime.md`：双 Agent、generic Assistant Runtime、StewardJob、consumer kind、受限写工具、去重现状。
- [ ] 更新 `.trellis/spec/architecture.md` 中直接受本任务影响的 Agent 边界，不改人物去重等其他 WIP 段落。
- [ ] 搜索生产代码中的 `steward_ping`、`min_kind`、`AgentJob(kind="steward")`、宽泛 `AGENT_KINDS` consumer 误用。
- [ ] 区分合法 `steward` 出现：StewardJob、maintenance、policy consumer、RAG tests、历史 migration/downgrade、任务文档。
- [ ] 确认后续 PersonalFamilyView/新用户推荐没有被本任务实现或弱化。

Search gate：

```bash
rg -n "steward_ping|min_kind|AgentJob\(.*steward|AGENT_KINDS" \
  backend agent .trellis/spec
```

## Phase 7 — Quality gate

先窄后宽：

```bash
cd backend && ../.venv/bin/python -m pytest -q \
  tests/test_agent_schema_contract.py \
  tests/test_agent_queue.py \
  tests/test_agent_tokens.py \
  tests/test_agent_tools.py \
  tests/test_agent_query_tools.py \
  tests/test_internal_agent_api.py \
  tests/test_memory_rag_service.py \
  tests/test_steward.py \
  tests/test_maintenance.py
cd backend && ../.venv/bin/python -m mypy app
cd backend && ../.venv/bin/python -m ruff check .
cd backend && ../.venv/bin/python -m ruff format --check .
cd agent && npm run type-check
cd agent && npm run lint
cd agent && npm test -- --run
cd agent && npm run build
```

后端全量：

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

若已知 `test_concurrent_double_accept_single_winner` 再次挂起，必须记录完整命令和超时证据，再运行：

```bash
cd backend && ../.venv/bin/python -m pytest -q \
  --deselect tests/test_ownership_transfer.py::test_concurrent_double_accept_single_winner
```

不得把 deselected 结果表述为“完整套件全绿”；该死锁由独立任务负责。

## Phase 8 — Docker Compose internal protocol E2E

- [ ] 使用临时/可清理 Compose 数据卷和完整 runtime flag 启动 API + Agent sidecar + openai-compatible stub。
- [ ] bootstrap、创建家庭主体/space、注册 Provider、space 选择 Provider。
- [ ] 创建 Assistant session/message；确认 lease 请求显式携带 `kind="assistant"`。
- [ ] 跑通 context → Provider proxy → tool/event → settle → SSE replay/Last-Event-ID。
- [ ] 省略 kind 和 `kind="steward"` 请求均 fail-closed。
- [ ] sidecar/response/log 不泄漏 Provider secret。
- [ ] 清理本次临时 Compose 资源；不删除用户已有卷。

## Phase 9 — Final review and handoff

- [ ] 运行 `trellis-check` 或等价全范围审查：spec 合规、跨层 schema、数据流、复用、测试和 diff scope。
- [ ] 检查 `git diff --check` 和 task-owned file list。
- [ ] 确认人物去重、前端 Wizard 和其他任务 WIP 未进入本任务提交。
- [ ] 将验证结果、E2E 证据、已知 deadlock 状态和 rollback 信息写入任务 notes/check 工件。
- [ ] 经用户确认后更新必要 spec、提交本任务 owned changes，并按 `trellis-finish-work` 收尾；未经授权不 push。

## Completion definition

只有在以下条件同时成立时才可报告完成：

- generic Pi Runtime assistant-only，所有非 Assistant 边界 fail-closed；
- StewardJob/maintenance 与 Steward shared-only policy consumer 均保留且有回归；
- migration 安全和 schema 保真已验证；
- backend/agent 静态与测试门禁、Compose internal E2E 有真实证据；
- 无人物去重或其他任务 WIP 混入；
- 任务文档与 spec 不再把 Steward 降格为非 Agent，也不再暗示 generic Steward runtime 存在。
