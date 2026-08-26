# 类型安全规范（初始规范 v0）

- tsconfig strict: true；禁止 any（必要时 unknown + 收窄）；禁 @ts-ignore（@ts-expect-error 需注释原因）。
- API 响应类型集中 types/api.ts，与后端 Pydantic schema 字段一一对应（人工同步，code review 对照）。
- **运行时校验**：api/ 层拦截器对响应做轻量校验（zod 或自写守卫），字段缺失/MASKED 结构不符时抛可观测错误而不是让 undefined 流入组件——遮罩结构 {__masked__: true} 必须有类型判别联合。
- 枚举值（dir_class/status/cal_type）用 const object + type 推导，与后端错误码常量表对齐。
- 日期处理只用 dayjs + lunar-typescript（或等价 lunar 库的 TS 版），禁止手写历法换算。

## V2.5 Memory/RAG 边界（2026-08-26）

- `MemoryCandidate`、`Memory`、`RAGDocument`、`RAGHit` 和 `ContextBuild` 的 API 字段必须从后端 schema 同步；scope 使用 `private`、`household:<space_id>`、`lineage:<space_id>`，不得在组件内改写为另一套枚举。
- Action/UI 只能通过 Pinia store 调用 memory/RAG API；候选确认、撤销、删除和 scope 切换成功后重新加载服务端状态，不做乐观本地副本。空间切换时清空上一空间的 memory/RAG 列表与引用。
- RAG 引用显示只能使用后端返回的 `citation_handle`、`source_type`、`source_id` 和 trust/sensitivity 标签；不把原始私人会话正文或未确认事实当作可信事实渲染。`[UNCONFIRMED FACT]` 必须保持可见。
- 结构化 ActionCard/Memory 引用使用共享 API 类型与运行时守卫；缺少 `revision`、`scope`、`citation_handle` 或状态字段时显示错误状态，不以 `undefined` 继续执行。
- 任意 masked/blocked/policy error 都是 fail-closed 的可解释 UI 状态；组件不得为便于展示而回退到原始 payload，也不得提供自动重试来绕过 provider/scope 限制。

### 验证

```bash
cd frontend && npm run type-check && npm run lint && npm test && npm run build
```

至少有组件或 store 测试覆盖：候选确认后服务端重载、private/household/lineage scope 标签、撤销后的引用失效、masked/blocked 错误、空间切换清理以及 Assistant 消息中的 `card_ids`/RAG citation 使用同一服务端状态。
