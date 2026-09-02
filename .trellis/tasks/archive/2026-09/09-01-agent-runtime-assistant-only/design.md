# Agent Runtime assistant-only 收口：技术设计

## 1. Design objective

本设计只解决执行边界混淆：generic Pi/会话 Runtime 只承载 Assistant，同时保留 Steward 作为独立底层 Agent 的运行和数据消费身份。

必须避免两种相反错误：

1. 继续允许 `AgentJob(kind="steward")`，形成 generic queue 与 `StewardJob` 双入口；
2. 把所有 `agent_kind` 都收窄为 Assistant，误删 Steward 在 Context/RAG/策略层的 shared-only consumer 身份。

## 2. Terminology and type boundaries

### 2.1 Runtime kind

```python
RuntimeAgentKind = Literal["assistant"]
RUNTIME_AGENT_KINDS = ("assistant",)
```

仅用于：

- `AgentSession.agent_kind`
- `AgentRun.kind`
- `AgentJob.kind`
- generic queue enqueue/lease/reaper/settle
- service/run token claims
- internal Agent API
- Assistant tool registry/allowlist
- Node sidecar lease/context/worker

### 2.2 Policy consumer kind

```python
PolicyConsumerKind = Literal["assistant", "steward"]
POLICY_CONSUMER_KINDS = ("assistant", "steward")
```

用于需要区分“谁在消费上下文/知识”的服务边界：

- RAG scope predicates
- ContextBuilder 输入校验
- policy/visibility tests and audit metadata

它不代表存在同名 `AgentRun`，也不允许 Steward 获得 Assistant token、工具或 sidecar。

类型放置应服从最小依赖：runtime 常量放在 `models/agent.py` 或独立的轻量类型模块；consumer 常量不得再从 generic runtime 表模型导入。优先使用已有模块中最小清晰定义，避免为两个字面量创建多层抽象。

## 3. Runtime topology

```text
Browser
  → FastAPI /api/agent
  → AgentSession(kind=assistant)
  → AgentRun(kind=assistant) ↔ AgentJob(kind=assistant)
  → /internal/agent/jobs/lease {kind:"assistant", leased_by}
  → Node sidecar
  → Pi session / Provider proxy / Assistant tools
```

Steward 不进入上述路径：

```text
DomainEvent
  → schedule_steward_job_for_event
  → StewardJob(space_id, trigger_cursor, policy_version)
  → maintenance worker lease/heartbeat/reaper
  → run_steward_job
  → DerivedFact / ActionCard / BehaviorProjection / findings / checkpoint
```

两条链可以使用相同的工程模式（SQLite immediate transaction、lease、heartbeat、reaper、幂等），但不共享持久化实体、token 或 worker 协议。

## 4. Backend contracts

### 4.1 Model and schema

- `AgentSession`/`AgentRun`/`AgentJob` CHECK 只接受 Assistant。
- `LeaseRequest.kind` 使用无默认值的 `Literal["assistant"]`，使字段显式必填。
- `LeaseOut.agent_kind`、`ContextOut.agent_kind` 和浏览器 Agent 输出使用 `RuntimeAgentKind`。
- `StewardJob` 保留自身 cause/status/FSM 和空间唯一索引，不引入 generic kind。

### 4.2 Queue and token

- enqueue 和 lease 在读写前校验 `RuntimeAgentKind`。
- `lease_next(kind)` 不再接受 `None` 作为“唯一队列”的隐式别名；HTTP schema 已保证显式值，服务层也 fail-closed。
- run token 只签发/接受 `agent_kind="assistant"`，并继续绑定 run/job/account/space/allowlist。
- downgrade 只恢复旧数据库表达能力，不改变现行服务层、token 或 sidecar 的 assistant-only 校验。

### 4.3 Tools

- Registry 的 kind 参数和 `required_kind` 使用 runtime kind，而不是 policy consumer kind。
- 删除 `familygraph.steward_ping` 注册项和 sidecar TypeBox/dispatch 表面。
- 旧工具名只保留在 negative tests 中，期望 `unknown_tool`。
- `record_term_usage` 保留；修正文档为“查询工具只读，称谓使用记录是经明确 consent 的受限写入例外”。

## 5. Steward policy consumer contract

### 5.1 Shared-only retrieval

Steward consumer 的 SQL 资格为：

- `d.status='active'`
- `d.confirmation_status IN ('confirmed','authorized')`
- `d.scope IN ('household','lineage')`
- `d.space_id = current StewardJob.space_id`
- 当前绑定的执行主体/空间授权满足 VisibilityPolicy 和 source-author visibility
- sensitivity/provider 规则不因 consumer kind 放宽

Steward consumer 必须排除：

- `scope='private'`
- 面向 Assistant 的 unrestricted/public 入口
- 其他 `space_id`
- 私人 Agent Session/Message/Memory
- platform/system admin 的全局权限

### 5.2 ContextBuilder persistence nuance

当前 `ContextBuild.run_id` 非空 FK 到 `agent_runs`，因此只有 Assistant generic Run 可以持久化 `ContextBuild`。本任务不为 Steward 创建伪 `AgentRun`。

设计处理：

- `ContextBuilder` 的 policy consumer 校验可接受 `steward`，以保持 shared-only policy seam 和测试；
- Steward 若使用 prefetched sources 且没有 generic run，`run_id=None` 时不创建 `ContextBuild`；
- 当前确定性 Steward 不调用模型或 ContextBuilder；未来模型辅助需要单独设计 `StewardContextBuild` 或 child-run 审计实体。

这既不阻断未来 Steward，又不重新引入 generic Steward Run。

## 6. Sidecar and internal protocol

- `InternalClient.lease()` 始终发送 `kind:"assistant"`。
- 后端缺少 `kind`、未知 kind、`steward` → 422；不得默认 Assistant。
- `getRunContext` 只接受 `agent_kind="assistant"`。
- worker 在 projection 校验后才创建 Pi session、Provider transport 或工具 handler。
- negative tests 需要证明 malformed/non-assistant projection 不触发 Provider request 或 tool dispatch。
- internal 协议的 authority 仍是 `backend/app/schemas/agent.py`；双侧变更后需要 compose 真实联调。

## 7. Migration 0024

### 7.1 Upgrade

1. 在任何 DDL 前查询三张 generic runtime 表的 kind。
2. 存在 null、steward 或未知值时抛出可诊断错误，保持旧表未改。
3. 临时关闭 SQLite FK，按现有列、FK 和 default 重建三张表。
4. 复制合法 Assistant 行。
5. 按依赖顺序替换旧表，重建：
   - active run partial unique index
   - lease scan index
   - session scope immutable trigger
6. 不重建 generic Steward space-active index。
7. 恢复原 FK PRAGMA 状态，并执行 `foreign_key_check`/schema assertions。

### 7.2 Downgrade

- CHECK 恢复 `assistant|steward`，并恢复历史 generic Steward index；
- 服务代码仍只接受 Assistant，因此 downgrade 不是产品 feature toggle；
- downgrade 后再 upgrade 的测试只在临时 `DATA_DIR` 运行。

### 7.3 Rollback

- 迁移冲突发生在 DDL 前，可直接修复/导出冲突数据后重试；
- 代码回滚与 schema downgrade 分开执行，禁止用删除冲突行作为自动回滚策略；
- 任何真实 generic Steward 历史数据都需要独立清理/转换计划。

## 8. Current WIP reconciliation

### Keep after review

- generic model CHECK assistant-only
- queue/token/internal API fail-closed
- sidecar non-assistant rejection
- removal of generic Steward concurrency/index semantics
- removal of `steward_ping`
- `required_kind`
- migration preflight refusal

### Correct before acceptance

- 将 generic `AgentKind/AGENT_KINDS` 与 policy consumer kind 分离；
- 恢复/新增 ContextBuilder 与 RAG 的 Steward shared-only tests；
- 不让 assistant-only 常量把 Steward consumer 判为 unsupported；
- `LeaseRequest.kind` 去掉默认值，queue service 不接受 `None`；
- 更新“Steward 是正式底层 Agent”的文档口径；
- 修正 tool-call 去重和“所有工具只读”的过期注释；
- 为 migration 的 schema/FK/index/trigger 保真增加定向断言。

### Exclude from this task

当前工作树中的人物去重、Steward duplicate audit、MemberCreateWizard 和 `zhconv` 依赖属于 `09-01-person-identity-dedupe`，不得混入本任务提交。

## 9. Compatibility and risks

### Compatibility

- 当前 sidecar 已显式发送 `kind="assistant"`，因此必填收紧不影响同仓实现。
- 省略 kind 的旧外部调用会被拒绝；项目无生产兼容窗口，接受破坏性收紧。
- DB 中如有 generic Steward 行，upgrade 拒绝而非猜测转换。

### Risks

1. **共享常量误用**：若 consumer 层继续导入 runtime kind，会再次抹掉 Steward。
   - 控制：分离命名 + Steward shared/private/other-space tests。
2. **迁移重建漂移**：SQLite 手写 DDL 可能漏列、default、FK、index 或 trigger。
   - 控制：反射 schema、`foreign_key_check`、合法数据 round-trip、downgrade/upgrade。
3. **WIP 混提**：人物去重文件与本任务交错。
   - 控制：按文件和 diff hunk 归属检查；只提交 task-owned hunks。
4. **文档概念退化**：把“不走 Pi Runtime”写成“Steward 不是 Agent”。
   - 控制：PRD/spec 固定双 Agent 口径和 topology。
5. **测试套件挂起**：既有 ownership transfer 并发测试有无界 Barrier/join 风险。
   - 控制：该测试由独立任务修复；本任务验证时先跑定向测试，完整套件如受阻要记录并运行排除该已知用例的证据，不能伪报全量通过。

## 10. Validation strategy

1. Python unit/contract tests：模型、queue、token、tools、internal API、ContextBuilder/RAG、StewardJob、迁移。
2. Node tests：client、tools、worker integration、strict context projection。
3. Static gates：mypy、ruff check/format、agent type-check/lint/build。
4. Backend broader suite，单独标注已知 deadlock test 状态。
5. Docker Compose 实链：bootstrap/provider/space/session/message → explicit Assistant lease → context/provider proxy/tool/events/settle/SSE。
6. 搜索门禁：现行生产代码没有 generic Steward enqueue/lease/tool；但 `steward` 仍存在于 StewardJob、policy consumer、RAG shared-only tests 和历史 migration/downgrade 文本中。
