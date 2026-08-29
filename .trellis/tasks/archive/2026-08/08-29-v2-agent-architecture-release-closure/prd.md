# V2 Agent 架构收口与发布阻断清零

> 任务状态：Planning（2026-08-29）
> 来源：`08-28-v2-audit-remediation` 的复审，以及 FamilyGraph v2 设计文档 00–08。
> 本任务只承接审计发现和架构收口，不代表任何实现已经完成；未得到后续明确批准前不得 `task.py start`。

## Goal

把 V2 从“Assistant 已接入 Pi、部分领域能力已落地，但 Steward、权限边界、协议一致性和运行证据仍有断层”收口为一个可审计的发布候选版本。最终必须同时满足：

1. Assistant 和 Steward 的产品职责、运行时和权限边界能够从代码与文档得到同一解释；
2. FastAPI 仍是身份、空间、事实、可见性、RAG、ActionCard 和审计的唯一业务真源；
3. P0/P1 安全与协议缺陷关闭，P2 有责任人、期限和缓解；
4. 每条验收结论绑定当前 commit、完整命令、退出码和可复核产物。

## Background and confirmed facts

### 已确认的实现成果（本任务不重复宣称为新完成）

- Assistant 已通过 `pi-coding-agent.createAgentSession()` 进入 Pi Agent loop；`pi-agent-core` 由 SDK 间接使用，`pi-ai` 负责 OpenAI-compatible Provider/流协议。
- RAG 关闭时 `ContextBuilder` 降级为空上下文，Assistant 不再因 503 阻断。
- Policy Guard 已将 `max_tokens`、`max_completion_tokens`、`max_output_tokens`、`stream_options`、`include_usage` 与凭据键区分；模型 literal 已设置 `compat.maxTokensField="max_tokens"`。
- Provider stream 的 `stopReason="error"` 不再被 settle 为成功。
- 原子建档、重复事件收口、reaper 终态、Memory/RAG 基础确认链、ActionCard 基础 FSM、前后端质量命令已有实质代码和单元测试。
- 当前没有生产部署、真实用户或真实成员数据，不需要迁移双写、回填或旧客户端兼容窗口。

### 核心架构结论

- **Assistant**：必须是 `account_id + session_id + space_id` 绑定的 Pi Session；模型只能通过版本化 FamilyGraph 领域工具读取安全投影。
- **Steward**：产品身份仍是按空间运行的系统 Agent，但正式关系结论、DerivedFact、推荐资格和 ActionCard eligibility 必须由确定性 `StewardEngine` 决定。Pi Steward 只作为受限的解释、歧义整理和编排层，不能替代图算法、权限、FSM 或 SourceFact 命令。
- **唯一 Job 真源**：`StewardJob(space_id + job_id + policy_version)` 是 Steward 领域作业真源；禁止再让通用 `AgentJob(kind="steward")` 形成第二套活跃队列。若启用 Pi 编排，Pi Run 必须作为该 StewardJob 的受限子执行，并共享同一 lease、scope 和终态。
- **Pi 分层**：`pi-ai` 是 Provider/模型协议；`pi-agent-core` 是 loop/tool/event 核心；`pi-coding-agent` 是 Session/Extension/资源加载 SDK；FamilyGraph 负责所有业务授权和持久化。Pi 不直连数据库。

## In-scope requirements

### R-ARC-01 Agent 架构与 Steward 收口（P0）

- 明确并实现 Assistant、StewardEngine、可选 Pi Steward Orchestrator 的边界、运行身份和工具清单。
- Steward 必须只消费当前 `space_id` 的确认事实、DerivedFact、TermRegistry、BehaviorProjection、确认共享知识和 checkpoint；不得读私人 Session/Memory、其他空间或 Web。
- 合并或停用 `AgentJob(kind="steward")` 与 `StewardJob` 的双重调度路径；一个空间最多一个活跃 Steward 作业，Pi 子执行不得产生第二个 lease/终态。
- 增加独立 Steward system prompt、context projection 和 tool allowlist；Steward 不得继续复用 Assistant prompt，也不得获得 `search_web`/`fetch_approved_page`。
- Pi 只负责自然语言解释、候选整理和编排；确定性关系计算、推荐矩阵、权限判定和正式写入仍由后端服务完成。

### R-SEC-02 可见性与网络边界（P1）

- 图出口在过滤节点后同步过滤关系边，隐藏节点不得泄露 ID、关系类型、标签或创建者。
- internal API 必须从公开 listener/宿主端口隔离；nginx 和宿主机均不能绕过网络边界访问 `/internal/agent`。
- 保留 service token + run token 的 typ、audience、scope、space、run/job、allowlist、TTL 负向测试；生产拒绝默认弱 secret。

### R-PROTO-03 Provider 与工具协议（P1）

- 建立唯一 ProviderGateway，统一 provider 解密、模型选择、readiness、超时/取消、usage、错误映射和脱敏；sidecar 不再依赖平行环境变量作为真实配置。
- 统一后端 registry、sidecar TypeBox、前端类型和文档的工具版本；修复 kinship 工具 v1/v2 记录与重放不一致。
- schema 递归校验 required、type、min/max length、numeric/array bounds、enum、nested object 和 `additionalProperties`；未知输入 fail-closed。
- 副作用工具的 `(run_id, tool_call_id, tool_version)` 必须原子占位并防并发重复执行。

### R-DATA-04 数据权利、RAG 与 Web（P1）

- 导出使用成熟 AEAD/envelope encryption、key id/轮换策略和一次性下载；只有成功解密后才消费下载资格，失败文件可清扫。
- RAG candidate 必须绑定来源 Session 的 `space_id`，不能把空间 A 对话确认进空间 B；删除、撤权、tombstone 先在查询谓词失效。
- Controlled Web 修复 DNS 校验与实际连接之间的 TOCTOU；fetch 按 search/citation 策略正确解析，不得固定读取 `research` 策略。
- Provider/sidecar 错误统一脱敏后再写日志、事件和 settle body；不得把上游响应原文当作公开错误。

### R-UI-OPS-05 前端隔离与发布证据（P1/P2）

- members、spaces、actionCards 等 store 增加 session generation/AbortController，登出、401、账号/空间切换后旧请求不得回写。
- 补齐 375×812 悬浮助手人工验收、空库 Compose、跨进程 E2E、backup/restore、FTS rebuild、SSE 历史和优雅停机证据。
- 修复当前 `ruff format --check backend` 失败，并记录所有命令、退出码、测试数量和产物。

### R-GOV-06 Trellis 工件一致性

- 本子任务的 PRD、design、implement、notes、handoff 和 JSONL 必须准确反映上述状态；不得把代码存在或旧任务摘要当作完成证据。
- 每条 AC 只在达到规定证据等级后勾选；P1/跨层/恢复/网络项要求至少 E2，发布级项要求 E3。
- 父任务仍保持 `in_progress`，本子任务在实现与检查完成前保持 `planning` 或 `in_progress`，不得提前归档。

## 已知部分完成与未完成矩阵

| 范围 | 当前判断 | 说明 |
|---|---|---|
| Pi Assistant 接线 | 已完成 | `session.ts` 使用 `createAgentSession`、in-memory session/settings、受限领域工具；需补当前 commit 的跨进程证据。 |
| RAG 关闭降级、token 脱敏、`max_tokens` 兼容、provider stream fail-closed | 已完成 | 已有回归测试；不等于整个 ProviderGateway AC 完成。 |
| 原子建档、ActionCard FSM、Memory/RAG 基础链路 | 基本完成 | 仍需跨空间、并发、删除传播和 E3 复验。 |
| Steward 领域算法 | 部分完成 | Python 确定性 worker 可执行，但未与 Pi Steward 运行模型和唯一队列闭环。 |
| Pi Steward | 未完成 | 只有 `steward_ping` 探针；缺专用 prompt、context、生产入口和真实 Pi job E2E。 |
| 图/关系边可见性 | 未完成，P1 | 隐藏节点被删但 `filtered_edges` 仍可能序列化隐藏端点。 |
| internal listener | 未完成，P1 | `/internal/agent` 与公开 API 共 listener，Compose 还发布 8000。 |
| ProviderGateway | 部分完成，P1 | DB provider 配置可进入 context，但仍由 sidecar 发起实际 Provider 请求，缺统一 egress/readiness/usage/rotation。 |
| 工具 schema/版本/幂等 | 未完成，P1 | 递归约束不完整；后端 kinship 版本为 2、sidecar 固定发送 1；查后执行存在并发窗口。 |
| Pi Guard 钩子 | 部分完成，P1 | `before_provider_request` 扩展 runner 会吞异常；当前硬阻断依赖 sidecar 直接调用 Guard，需明确并验证真正的 fail-closed 边界。 |
| 导出加密 | 未完成，P1 | 当前为自制 XOR+HMAC，非成熟 AEAD；密文损坏前已消耗下载资格。 |
| RAG/Web/错误脱敏 | 部分完成，P1 | RAG 缺 session-space 绑定；Web 有 DNS TOCTOU 和 policy 固定；provider error 仍可原样进入日志。 |
| 前端缓存/人工验收 | 部分完成，P2 | 多 store 无 generation/abort；375px 人工走查未形成证据。 |
| 运维恢复/发布证据 | 未完成，P1 | guga 持续 503；空库 Compose、第二新卷恢复、FTS/SSE/优雅停机证据缺失。 |
| Trellis 状态 | 未完成，P0 | 父任务 handoff、AC、implement、commit 元数据与现实不一致；本子任务必须追加式更正。 |
| Steward 生产调度 | 未完成，P1 | 确定性执行器主要由服务/测试暴露，缺少与 canonical `StewardJob` 绑定的生产 scheduler/worker 入口。 |

## Out of scope

- 不新增 MatchBroker、陌生人匹配、多空间单 Session、物理合并家族空间、自动发申请或自动扩大公开范围。
- 不把 LLM 变成亲属图算法、SourceFact 真源、权限判定、推荐 eligibility 或导出加密器。
- 不开放任意 SQL、shell、文件、MCP、浏览器自动化或 unrestricted HTTP。
- 不做生产数据迁移、双写、回填或旧客户端兼容窗口。
- 不把所有聊天自动写入 RAG，也不采集键鼠、停留时长等泛行为。

## Acceptance Criteria

- [ ] **AC-ARC-01**：当前代码和文档能明确说明 `pi-ai`、`pi-agent-core`、`pi-coding-agent` 和 FamilyGraph 的边界；Assistant 使用真实 Pi Session，Steward 使用独立专用上下文和工具清单。
- [ ] **AC-ARC-02**：单空间 Steward 只有一个 canonical `StewardJob`；确定性引擎、可选 Pi 编排、lease、checkpoint、终态和审计之间不存在第二套活跃队列。
- [ ] **AC-SEC-01**：隐藏节点对应的关系边、标签、创建者和端点 ID 在 graph/search/agent 各出口均不可泄露。
- [ ] **AC-SEC-02**：宿主机、nginx、浏览器不能直接访问 internal listener；错误 token、默认 secret 和 scope 越权均有负向测试。
- [ ] **AC-RT-01**：ProviderGateway 是唯一 Provider 出口；DB 配置、readiness、usage、timeout/cancel、rotation、错误脱敏和 local-required/no-fallback 有跨进程证据。
- [ ] **AC-RT-02**：后端、sidecar、前端 schema 快照逐字一致；v1/v2 重放、完整递归约束、未知字段和并发 tool_call 均 fail-closed 且幂等。
- [ ] **AC-DATA-01**：导出采用成熟 AEAD envelope；损坏/过期/撤销/崩溃恢复不泄露明文且不错误消费下载资格。
- [ ] **AC-DATA-02**：RAG 来源绑定 account+session+space；tombstone/撤权先失效查询，Controlled Web 无 DNS TOCTOU、策略错配和 Steward Web 工具。
- [ ] **AC-ISO-01**：双用户双空间、登出/401/空间切换、Steward/private RAG、ActionCard 和前端 store 均无旧 scope 回写或跨空间命中。
- [ ] **AC-OPS-01**：空库 Compose、迁移、健康、优雅停机、online backup/restore、integrity_check、FTS rebuild、SSE 重放和合成数据 E2E 在当前 commit 可重跑。
- [ ] **AC-GOV-01**：父/子任务工件、JSONL、状态、commit、证据和风险 notes 一致；所有 P0/P1 关闭，P2 有接受者/期限/缓解后才允许归档。

## Definition of done

1. 上述 AC 逐项具有 E2/E3 证据，不能用“测试数量”替代跨层断言。
2. Steward 的唯一 Job/Run 模型、Pi 分层和回滚策略已写入代码注释、`.trellis/spec/` 或任务附录。
3. 所有 P1 安全与协议问题关闭；残余 P2 具有明确 owner、期限和 feature flag/kill switch 缓解。
4. 新任务和父任务的实时状态、commit、handoff、notes 和验证报告互相一致。
5. 只有完成最终复审、用户确认并满足父任务发布门禁后，才允许 `task.py archive`。

## Blocking open questions

无产品级阻塞问题。AEAD 库、internal listener 拆分方式和 Provider readiness 探针可由实现者在 `design.md` 选定，但不得改变本 PRD 的权限、队列和 fail-closed 合同。
