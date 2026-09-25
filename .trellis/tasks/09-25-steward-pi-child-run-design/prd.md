# Steward Pi child run 架构重设计

> 状态：Planning。本任务**只产出设计**（PRD / design / implement），不修改任何代码。
> 交付物是可供审阅的架构方案与后续子任务图；实施按 `implement.md` 的 Stage 1..3 拆为独立子任务。

## Goal

把 Steward 的模型辅助从「进程内裸 httpx 直连 Provider」改回 `08-26-v2-agent-system` 的原始设计——**Assistant 与 Steward 都跑 Pi 运行时**——为此定义 StewardJob ↔ Pi child run 的父子作业、凭据、上下文、工具与审计合同，并给出可分批落地、可回退、不破坏现有确定性内核与安全红线的迁移路径。

## 背景：当前背离了什么

用户原始意图记录在 `.trellis/tasks/archive/2026-08/08-26-v2-agent-system/`：

- `prd.md:7`：「新增一套**以 Pi Agent 为底层运行时**…的双 Agent 系统」。
- `design.md` 架构图：`Steward Scheduler --> Q`（与 Assistant 同一 durable queue）`Q --> PI`（同一 sidecar）。
- `08-26-v2-1-agent-runtime/design.md:5`：「交互 Run 与 **Steward Job 都写入同一 durable queue，由 sidecar lease**」。

实际发生三次收窄，每次都留下未闭合项：

| 时点 | 动作 | 留下的缺口 |
|---|---|---|
| 08-29 `v2-agent-architecture-release-closure` | 把 Pi Steward 从「已实现」降级为「可选 Orchestrator」 | `implement.md` 两条关键项至今未勾选：为 Steward 建立独立 prompt/context/tool allowlist 合同；为 child run 建立与 StewardJob 的一对一关联、继承 scope/policy/lease/取消与统一终态。其 `notes.md` §6 要求「若最终不启用 Pi，必须明确记录确定性内核为生产实现」，**该待办从未执行** |
| 09-01 `agent-runtime-assistant-only` | runtime 收为 `assistant`（`RUNTIME_AGENT_KINDS=("assistant",)`、迁移 0024 把三表 kind CHECK 收为 `= 'assistant'`、lease 端点硬拒非 assistant） | 其 R1 写「未来若引入 Pi Steward child run，**必须另立任务**」——该任务**从未创建** |
| 09-06 `steward-model-assist` design B1 | Steward 模型调用改为 in-process 直连 Provider（`resolve_runtime` + 裸 httpx） | 绕开 `provider_proxy`，因此没有 `agent_provider_egress` 审计、没有上游错误分类/分层重试治理、没有 run 级可观测性、没有工具循环与多轮 |

**本任务要关闭的正是 09-01 那句「另立任务」。**

## 已核实依据（只读核查，本轮未改动任何代码）

运行时合同：

- `backend/app/models/agent.py:40` `RUNTIME_AGENT_KINDS: tuple[RuntimeAgentKind, ...] = ("assistant",)`；`_AGENT_KIND_CHECK_SQL = "agent_kind = 'assistant'"`；`AgentJob` 与 `AgentRun` 均为 `kind = 'assistant'` CHECK。
- `backend/app/models/agent.py`：`AgentSession.account_id` / `space_id` 均 `NOT NULL`；`AgentRun.job_id` 为 `nullable=True, unique=True`；`AgentRun.message_id` 可空；`AgentRun.runtime_snapshot_json` 保存不可变 Provider 快照。
- `backend/migrations/versions/0024_agent_runtime_assistant_only.py`：`_validate_existing_kinds()` 在 DDL 前 fail-closed 拒绝不可表达的 kind；`_rebuild_runtime_tables()` 重建 CHECK 并保留行/FK/trigger；downgrade 恢复 `('assistant','steward')` 并重建 `uq_agent_jobs_space_active`。
- `backend/migrations/versions/0009_agent_runtime.py:160`：历史 `uq_agent_jobs_space_active` 索引 `WHERE kind='steward' AND status IN (...)` —— 即**队列层**曾经承担 Steward 分区，这是 09-01 明确要消除的第二队列。
- `backend/app/schemas/agent.py:29` `LeaseRequest.kind: Literal["assistant"]`；`:37` `LeaseOut.agent_kind: AgentKind`；`:92` `ContextOut.agent_kind`。
- `backend/app/services/agent_tokens.py`：`issue_run_token` / `decode_run_token` 都校验 `agent_kind in RUNTIME_AGENT_KINDS`；service token TTL 默认 120s、run token ≤600s。
- `backend/app/api/internal_agent.py:284` lease 端点对 `body.kind != "assistant"` 返回 403；`:196-240` `_authorize_run` 五元组核验 + **每次请求重算 active membership**。
- `backend/app/services/agent_execution.py`：`ExecutionIdentity` + `fence_execution()` 要求 `Account` 存在且 `SpaceMember(status='active')`；`acquire_run_writer()` 用 no-op UPDATE 取写锁。
- `backend/app/services/provider_proxy.py`：`_require_executable_run` / `_refresh_run_gate` / `_admit_upstream_request` / `stream_provider_response` / `passthrough_with_audit` **全部以 `AgentRun` 为参数**，内含取消门禁、上游 4xx 保真、`x-should-retry`、流中复核与 `agent_provider_egress` 审计。
- `backend/app/services/agent_provider.py:489` `resolve_for_run(db, run, space_id, agent_kind)`：`agent_kind` 已是参数，快照字段结构可复用；注释已预留「steward child run 快照扩展属子任务 B」。
- `backend/app/api/admin_agent_latency.py`：只按 `created_at` 选 `AgentRun` / `AgentRunEvent`，**无 kind 维度**；`_provider_retry_windows` 以 `AuditLog.target_id == run_id` 关联 `agent_provider_egress`。
- `backend/app/services/admin_read_model.py:697` `agent_runs()` 返回 `account_id=agent_session.account_id`。
- `backend/app/api/agent.py:119` `_own_run_or_404` 以 `agent_session.account_id != account_id` 判 404。

Steward 现状：

- `backend/app/services/steward_assist.py`（2212 行）：三阶段（注册 / 调度预留 / 写回），`_post_json_async` 用 `httpx.AsyncClient` + `asyncio.timeout` 直连 `runtime.base_url + _API_PATHS[api]`；`_reserve_attempt` 预留 `StewardModelCall(reserved)`；`_settle_attempt` 保守计费 + 封闭 schema 校验；`_apply_batch` 在写回栅栏后 CAS 应用；`recover_stuck_batches` 覆盖四个崩溃点。
- `backend/app/services/steward_guard.py`：受众限定输入投影（节点代号 `n001..`，绝不发 `User.name`）、`outbound_check` 复用 `policy_guard.before_provider_request`、封闭输出 schema 校验器（候选/严格排列/解释）。
- `backend/app/models/steward.py`：`StewardJob`（space 分区、lease/heartbeat/attempt/checkpoint）、`StewardAssistBatch`（`job_id` UNIQUE、独立 lease、`fence_json`）、`StewardModelCall`（attempt 状态机 `reserved→in_flight→succeeded|failed|degraded|unknown|skipped`）。
- `backend/app/services/maintenance.py`：core 泵 → `steward_delivery.drain` → `recover_stuck_batches` → `schedule_due_batch` → `launch_batch`（有界线程池、自有 Session、HTTP 在事务外）。
- `backend/app/services/steward.py:1020` `_LeaseHeartbeat`：core job 的独立心跳线程（interval = `STEWARD_LEASE_TTL_SECONDS/3`）。
- 迁移链 head：`0054_seed_household_roster_fix`（新迁移应为 `0055_*`）。
- 测试面：`_post_json` / `transport=` 在 4 个测试文件中共 66 处引用。

Sidecar 现状：

- `agent/src/worker.ts`：`this.active !== null` → **严格串行**，每时刻至多一个 run；心跳 interval = `defaultLeaseMs/3`（60s lease → 20s）。
- `agent/src/session.ts`：`buildRunSession()` 硬编码 `systemPrompt: ASSISTANT_SYSTEM_PROMPT`；`resolveProvider()` 强制 `base_url` 必须以 `/internal/` 开头且 `api_key === null`；`SessionManager` id 固定为 `fg-${account_id}-${session_id}` 以稳定 prompt cache key。
- `agent/src/client.ts:211,237,471` 与 `worker.ts:196` 多处硬判 `agent_kind === "assistant"`。
- `agent/src/tools.ts` `TOOL_VERSIONS` 全部为 assistant 领域工具；`agent_tools.default_allowlist()` 以 `required_kind="assistant"` 注册。
- `agent/src/prompt.ts` 只有 `ASSISTANT_SYSTEM_PROMPT`。
- `docker-compose.yml:162` `agent` 服务挂 `backend` 网络（`internal:true`，无外网 egress），env 仅 `FG_API_BASE_URL` / `FG_INTERNAL_API_BASE_URL` / `AGENT_SERVICE_SECRET` / `AGENT_RUNTIME_ENABLED` / `HEALTH_PORT`。

## Requirements

### R1 恢复「双 Agent 同运行时」

- Steward 的模型执行必须回到 Pi 运行时：受限 child run，而不是进程内裸 HTTP。
- Assistant 与 Steward 共享同一套**执行**合同：run 状态机、lease/heartbeat/attempt、run token、context 投影、事件流、provider 网关、egress 审计、错误分类与分层重试、取消门禁。
- 「共享执行合同」**不**等于「共享队列」。`agent_jobs` 保持 assistant-only：Steward child run 的 lease 来自 `StewardJob`/`StewardAssistBatch`，**禁止**在 `agent_jobs` 里重建 `kind='steward'` 第二队列（09-01 已记录的红线）。

### R2 父子作业关系

- 每个 child run 必须能唯一追溯到 `(space_id, steward_job_id, assist_batch_id?, model_call_id?)`。
- `StewardJob` 仍是唯一 canonical 空间作业：每空间至多一个 active job；child run 不得产生第二套活跃队列或第二个终态。
- child run 的终态与 `StewardModelCall` attempt 状态必须由**同一领域服务结算**，不得出现「job 成功 / child run 失败」的双终态不一致。
- child run 继承父 job 的 `policy_version`、空间 scope、Provider 解析维度（`agent_kind="steward"`）与取消语义。

### R3 凭据与信任边界

- sidecar 永不持有 Provider 凭据：child run 的 `base_url` 必须是站内代理路径 `/internal/agent/...`，`api_key` 恒为 `null`（沿用现有合同）。
- child run 使用独立 run token，claims 绑定 `run_id / steward_job_id / attempt / agent_kind="steward" / space_id / viewer_account_id? / tool_allowlist`，TTL 上限 600s。
- 浏览器面（`/api/agent/*`）**不得**暴露 Steward child run 的 run/message/event；admin 面只读且脱敏。
- 空间成员资格与授权在**每次**内部请求重算，与 Assistant 同判据。

### R4 上下文与输入投影

- child run 的 prompt 输入仍由服务端按 `steward_guard` 的白名单投影生成（节点代号、已确认 fact id/type/revision、minor 标记）；**绝不**把 `User.name`、masked 原值、健康/住址等高敏感字段、私人 Session/Memory 交给 sidecar 或模型。
- Steward 有自己的 system prompt 常量，**不得**复用 `ASSISTANT_SYSTEM_PROMPT`（08-29 未闭合项）。
- context/input 策略（`policy_guard` 对应 hook）在服务端构造投影时执行；`before_provider_request` 在网关出站前复核。
- Steward consumer 仍是 shared-only：不得读取 private memory/session、其他空间或 platform operator 全局数据。

### R5 工具面

- Steward child run 有**独立** tool allowlist，与 assistant allowlist 不相交、不互相继承。
- Steward 工具只读；任何写类工具仍受 `(run_id, tool_call_id)` 去重表红线约束（现状：写类工具未注册，本任务不注册）。
- 工具调用必须经服务端鉴权、schema 校验与审计，sidecar 不得绕过。
- 现有 assistant 工具（`required_kind="assistant"`）不得因本设计而放宽给 Steward；Steward 需要的读取能力以独立注册项表达。

### R6 输出校验与写回

- 保留现有封闭 schema 校验（候选只允许原子 `SOURCE_FACT_TYPES`、排序必须是严格排列、解释只允许声明槽位与证据 id）与证据围栏。
- 保留写回栅栏 `_fence_check`（开关 / policy_version / provider revision / 证据摘要 / 卡片 revision / lease）与 CAS 应用。
- 保留 attempt 级预算（调用次数、token 上界、prompt 字节上界）与保守计费语义（`unknown` 计费且不自动重放）。
- 保留四个崩溃恢复点与 `recover_stuck_batches` 的收敛语义。

### R7 可观测性与审计

- 每次真实出站写恰好一条 `agent_provider_egress` 审计（含 `error_class` / `retryable` / `sent`），`target_id` 为 child run id。
- Steward 与 Assistant 的延迟/重试统计**不得混成同一分布**：`/admin-api/v1/agent/latency` 必须能按 kind 分列。
- 内部证据（`timing_json`、`context_reference_json`）仍永不进入 `public_payload`。

### R8 回退与开关

- 四种 assist（`candidate` / `ranking` / `explanation` / `terminology`）必须能**逐个**迁移，每个都能独立开关与独立回退到现有 in-process 路径。
- 部署级 kill switch 关闭时，Steward 行为与当前确定性基线（含现有 in-process 辅助）等价。
- 回退不得留下孤儿 child run：回退时在途 child run 必须收敛为明确终态并释放 attempt 预留。

### R9 迁移安全

- 迁移必须在**任何 DDL 或 Alembic 版本移动之前**执行 refusal guards（沿用 0024 的模式），发现不可表达数据即中止并报告表/行/值。
- downgrade 不得破坏性恢复旧种子值或删除已产生的 child run 证据行。
- 迁移先在隔离 `DATA_DIR` 验证 `upgrade head → downgrade → upgrade`，再运行受影响测试。

### R10 本任务范围

- 本任务**只产出设计**：`prd.md`、`design.md`、`implement.md`。
- 不写代码、不改迁移、不动 `agent/`、不动 `docker-compose.yml`。
- 不把 `09-11-steward-capability-followups` 的延期能力（shared RAG 辅助、个人路径模型解释、地区称谓包）或 `09-11-steward-cross-space-discovery` 的跨空间发现拉进本设计；只在 design 中标注「Pi child run 落地后这些能力才具备实现前提」。

## Acceptance Criteria

- [ ] **AC-1 背离闭合**：`prd.md`/`design.md` 明确记录 08-26 原始设计、三次收窄的时点与各自未闭合项，并给出本设计如何闭合 09-01 的「另立任务」要求。
- [ ] **AC-2 边界裁定**：design 明确回答「child run 复用 `agent_sessions`/`agent_runs`/`agent_run_events`，还是另立一套」，并逐条列出该裁定的代价（kind CHECK 重开、`account_id` 可空、`fence_execution` 分流、admin 读模型、latency 口径、浏览器端点隔离）。
- [ ] **AC-3 队列红线**：design 明确 `agent_jobs` 保持 assistant-only，说明 child run 的 lease 来源，并证明不会重建 `kind='steward'` 第二队列。
- [ ] **AC-4 父子与终态**：design 给出 `StewardJob ↔ child run ↔ StewardModelCall` 的关联字段、唯一性约束、状态映射表，以及「同一领域服务结算、无双终态」的实现点。
- [ ] **AC-5 凭据与 token**：design 给出 child run token 的 claims 全集、签发点、校验点、TTL 与重放防护，并说明为何 sidecar 仍拿不到 Provider 凭据。
- [ ] **AC-6 上下文与 prompt**：design 给出 Steward system prompt 的归属与版本化方式、context 投影字段白名单、以及「不得复用 assistant prompt」的实现点。
- [ ] **AC-7 工具面**：design 给出 Steward tool allowlist 的构成、注册表表达方式（`required_kind` 语义）、与 assistant 的隔离断言。
- [ ] **AC-8 保留的红线清单**：design 逐条列出被保留且不得削弱的现有合同（封闭 schema、写回栅栏、预算与保守计费、四个崩溃点、evidence fence、shared-only），并给出各自的回归入口。
- [ ] **AC-9 可观测性**：design 说明 `agent_provider_egress` 与 latency 端点如何按 kind 分列，避免两套种群混算。
- [ ] **AC-10 分批与回退**：design + implement 给出 Stage 1..3 的独立可验收切片、每片的开关、每片的回退动作与回退后孤儿 run 的收敛方式。
- [ ] **AC-11 迁移安全**：design 给出迁移的 refusal guard 清单、隔离 `DATA_DIR` 验证命令、downgrade 语义。
- [ ] **AC-12 子任务图**：implement 给出后续子任务的拆分、顺序、各自验收命令与依赖（依赖写在子任务文档里，不靠树位置隐含）。
- [ ] **AC-13 风险与代价如实登记**：design 明确写出该改动的风险面（assist 状态机、provider egress、live CHECK 重建）、不可逆点、以及需要用户裁定的开放问题。
- [ ] **AC-14 不越界**：仓库中除本任务目录外无任何文件改动；未把延期能力或跨空间发现拉入范围。

## Out Of Scope

- 任何代码、迁移、compose、sidecar、前端改动。
- Steward 会话化（面向用户的聊天入口）。
- LLM 参与授权、状态机、可见性判定。
- 为 Steward 注册任何有副作用的工具。
- `09-11-steward-capability-followups` 与 `09-11-steward-cross-space-discovery` 的延期能力。
- 跨空间亲属发现、MatchBroker。
- 降低 assistant 侧重试预算（该决策独立待批）。

## 裁定结果（2026-09-25，无开放项；详见 `design.md` §16）

原六个待裁定问题：

- **Q1 进程拓扑**：现有 sidecar 串行复用 / 现有 sidecar 改并发 / 新增 `agent-steward` 容器？
- **Q2 执行边界**：child run 复用 `agent_runs`/`agent_run_events` 还是另立一套 run 表？
- **Q3 迁移节奏**：四种 assist 是「先迁 terminology 一个」还是「四种一次迁完」？
- **Q4 会话语义**：Steward child run 是否需要跨 run 的会话历史？
- **Q5 工具化**：与迁移同批，还是独立成阶段？
- **Q6 membership 替代判据**：分层判据，还是收紧到与 assistant 同级？

**用户裁定**：Q1=**现有 sidecar 改多槽并发**（`FG_AGENT_ROLE=both`）；Q2=**复用 `agent_runs`/`agent_run_events` + 窄表 `steward_runs`**；Q3=**先迁 terminology 一类**；Q4=**无跨 run 历史**；Q5=**工具化独立成 Stage 3**；Q6=**接受分层 membership 判据**（残留风险与回归义务见 `design.md` §5.2.1）。

> Q1 选 A2 使 `worker.ts` 的单槽→多槽改造成为**本设计最大的风险项**（动 assistant 热路径）；改造点与新增回归见 `design.md` §7.2/§7.4，若回归面失控可回退 A3（新增容器，§7.6）。
