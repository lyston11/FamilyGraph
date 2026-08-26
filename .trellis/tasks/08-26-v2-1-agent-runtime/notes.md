# V2.1 注记

- Pi 能注册任意自定义工具，但“能”不等于“应直接接 DB”；本项目锁定 FastAPI domain tool 边界。
- `session.subscribe` 只观察；需要拦截的 `tool_call/context` 必须使用 `pi.on`。
- `context` 每轮触发，重查询应由 ContextBuilder 预取，不在 hook 内执行。
- 持久化 SSE 借鉴 LearnGraph，但不复制其完整 workspace/sandbox/MCP 架构。
- 不静默 fallback 同时是隐私、成本和可复现性合同。
