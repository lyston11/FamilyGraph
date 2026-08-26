# Pi Agent 与 LearnGraph 研究结论

## 范围

- Pi 教程：`/Users/lyston/Obsidian/lyston/Codex/Agent 与 AI 系统/Pi/dg-ai-notes 教程/` 的 ch01–ch10、pr02–pr07。
- LearnGraph：`/Users/lyston/Obsidian/lyston/Codex/项目与服务/LearnGraph/` 的 00–13 架构专题；部署凭据与服务器操作记录不属于本设计依据。

## Pi 的准确定位

- `pi-ai` 统一 Provider/模型协议；`pi-agent-core` 提供 loop/tool/event；`pi-coding-agent` 提供 Session、extension、skill、compaction；TUI 与业务 Web UI 正交。
- Pi 不等于业务 Agent 平台，也不会替 FamilyGraph 提供 ACL、领域事务、RAG scope、推荐状态机或数据权利。
- 工具管线为 prepare arguments → schema validate → beforeToolCall → execute → afterToolCall（`ch05-工具系统.md:133-244`）。
- `pi.on` 是会被等待并可改变行为的决策通道；`session.subscribe` 是适合流式输出和观察的广播通道（`pr06-事件系统.md:251-311`）。
- `input/context/tool_call/tool_result` 等决策点只应由扩展处理；`context` 每轮模型调用都会触发，不能做重 DB 查询（`pr06-事件系统.md:322-348,605-607`）。
- `before_provider_request` 可检查最终 Provider payload；`agent_settled` 是 prompt 收尾的可靠信号（`pr06-事件系统.md:410-425`）。

## LearnGraph 可复用原则

- 模型只产生 function call，后端根据 workspace/actor/ACL/状态/schema/超时/输出上限执行，运行时不提供任意 HTTP、shell 或 fallback（`03 LearnGraph Agent 工具系统与能力边界.md:20-32`）。
- 工具定义披露、turn-local 激活、执行期授权和领域服务授权是不同层，不能用“模型看见工具”代替 grant。
- 图/证据写入遵循 proposal → 用户确认 → revision/不变量校验 → publish；对应 FamilyGraph 的 SourceFact 治理。
- Memory 事件、查询投影、Provider context 与 chat/SSE 事件是不同记录链（`05 LearnGraph Memory 事件溯源与长期记忆设计.md:31,209`）。
- ContextBuilder 必须先按 scope/sensitivity 过滤，再按预算排序；检索到不等于注入，注入也不获得系统指令权（`06 LearnGraph Context Builder 与上下文工程.md:18-22`）。
- SSE 持久化并支持 Idempotency-Key 与 Last-Event-ID，断线恢复不重新执行工具（`01 LearnGraph 后端核心运行时与 Agent 引擎.md:141-171`）。

## 采用

- Node sidecar + FastAPI 领域真源。
- 版本化领域工具、执行期重授权、持久化 Run/Event/Job。
- `pi.on` 构建 Policy Guard，`subscribe` 构建事件桥。
- Proposal/ActionCard 写入治理、scope-first ContextBuilder、持久化 SSE。

## 不照搬

- 不复制 LearnGraph 的 workspace、sandboxd、MCP/Skill grant、复杂多 Provider 路由与重型图证据系统。
- 不继承 pi-coding-agent 默认 read/write/edit/bash。
- 不引入任意 shell、文件、外网或独立向量数据库作为首版前置条件。
- 不 fork Pi；只有 SDK/扩展无法满足已验收合同且上游无可用扩展点时才重新评估。
