# Agent Runtime assistant-only 收口：决策记录

## 2026-09-01 · 产品语义纠偏

最初审查根据当前代码形态把 Steward 描述为“确定性后端维护引擎，而不是第二个 Agent”。用户指出这与此前设计不一致：

> 在两个家庭、家族成员之间的关系确认；给刚注册的用户推荐家族用户；为每一个用户规划出他独有的家族树、家族亲属关系称谓等；都需要一个底层运行的 agent 来做，而 Steward 就是这样才被设计出来的。

恢复此前 Claude 会话后，确认用户已经明确：

> Steward 是底层引擎 agent，会说话的管家是另一个 agent 助手。

因此本任务从“系统只保留一个 Agent”纠正为“双 Agent、两种运行边界”：

- Assistant 是面向用户的会话式 LLM Agent，也是唯一进入 generic Pi Runtime 的 Agent。
- Steward 是事件驱动、按空间分区、长期运行的底层引擎 Agent，通过 `StewardJob`/maintenance 运行。
- assistant-only 只约束 `AgentSession`、`AgentRun`、`AgentJob`、token、lease、sidecar 和 Assistant tool registry。
- assistant-only 不能删除 Steward 正式身份、StewardJob 链路或 shared-only policy consumer 身份。

## 已确认决策

### Runtime 和 consumer kind

- `RuntimeAgentKind = "assistant"`。
- `PolicyConsumerKind = "assistant" | "steward"`。
- 禁止继续用同一 `AGENT_KINDS` 同时表达 generic runtime 和 Context/RAG consumer。
- `LeaseRequest.kind` 显式必填；省略、`steward`、未知值均 fail-closed。

### Steward 运行和数据边界

- generic `AgentJob(kind="steward")` 是历史遗留入口，应移除。
- Steward 的正式执行单元是 `StewardJob(space_id, job_id, policy_version)`。
- Steward 可以消费当前空间 confirmed/authorized shared 数据，不能读取 private Session/Memory 或其他空间数据。
- 不为 Steward 伪造 generic AgentRun；未来模型 child run/context 审计另立任务。

### 工具边界

- 删除 `familygraph.steward_ping`，不保留兼容别名。
- Assistant 继续保留经明确 consent 的 `record_term_usage` 受限写入例外。
- `record_term_usage` 不能修改人物、关系、成员资格或 SourceFact，并继续使用 `(run_id, tool_call_id)` 幂等台账。

### Steward 能力方向

- Steward 负责 PersonalFamilyView、关系/称谓派生、关系候选、推荐资格和一致性审计。
- 采用确定性关系与权限核心；未来模型只能辅助候选、排序和解释。
- Steward 不自动确认 SourceFact、创建成员、扩大可见权、发送申请、合并空间或替用户接受建议。

## 当前 WIP 纠偏清单

现有 assistant-only WIP 的模型、queue、token、internal API、sidecar、tool 和迁移方向大体保留，但必须纠正：

- 恢复/保留 `ContextBuilder` 与 RAG 对 Steward shared-only consumer 的识别；
- 恢复 private/other-space 拒绝回归；
- 不让 assistant-only runtime 常量误拒绝 Steward policy consumer；
- 去掉 lease kind 默认值和 queue 的 `None` 隐式行为；
- 更新文档，不能再写成“Steward 不是 Agent”；
- 修正“所有工具只读”和“V2.4 才有 tool-call 去重表”等过期说明。

## 关联后续任务

- `09-01-personal-family-view`：完整保存个人家族视图、多树连接、配偶边界和三种归属分离。
- `09-01-new-user-family-recommendations`：后续决定初始化触发点和推荐闭环。
- `09-01-person-identity-dedupe`：同步重复阻止与 Steward 回溯审计，不得混入本任务提交。

## 2026-09-01 · 本次执行验证

已完成本任务范围内的 runtime/consumer 分层修正：

- 新增 `app.services.policy_consumer`，定义 `PolicyConsumerKind` 与 `POLICY_CONSUMER_KINDS`；generic runtime 改用 `RuntimeAgentKind`/`RUNTIME_AGENT_KINDS`。
- `LeaseRequest.kind` 无默认值；HTTP lease 与 `agent_queue.lease_next` 对缺失、未知、`steward` 均 fail-closed。
- `ContextBuilder` 接受 Steward shared-only consumer，但拒绝以 Steward 伪造 `ContextBuild` 所需 generic `AgentRun`；RAG 对 Steward 禁止 private/public unrestricted 分支。
- 正向 internal lease 测试已同步显式 `kind=assistant`；旧省略 kind 作为 422 回归输入保留。

验证证据：

- backend Agent Runtime/Steward/RAG 定向测试：124 passed。
- backend 排除已知 ownership transfer deadlock 用例：615 passed, 3 skipped, 1 deselected。
- backend ruff check、ruff format --check、mypy：通过。
- agent vitest：87 passed；type-check、lint、build：通过。
- 临时 `DATA_DIR` Alembic upgrade→downgrade→upgrade：通过。
- `task.py validate 09-01-agent-runtime-assistant-only`：通过。
- `docker compose config --quiet`、API health、容器内 sidecar readyz：通过；当前运行栈报告 cloud/local provider missing，因此尚未执行需要注册 Provider 和真实模型代理的完整 Compose internal protocol E2E。

仍待完成：完整 Compose bootstrap/provider/session/message→显式 Assistant lease→context/provider/tool/settle/SSE 联调，以及最终 task-owned diff 审查、必要 spec 更新和提交。

`prd.md`、`design.md` 和 `implement.md` 是本任务最终范围与执行权威；本 notes 保存此次关键纠偏及其原因。
