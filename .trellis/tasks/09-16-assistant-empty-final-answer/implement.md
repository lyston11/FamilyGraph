# 助手空最终回答：执行计划

> 设计见 `design.md`。**R1 与 R2 必须同批发布**（只做 R2 会把「可见空气泡」变成「静默成功」，比现状更糟）。

## 前置

- [x] 核查完成（`design.md` §0：推翻「生产已发生」的初判，确认路径可达 + 三层证据）
- [ ] `implement.jsonl` / `check.jsonl` 配置 spec 上下文

## 实施步骤

### A. sidecar：事件过滤 + 结算判定（同批）

1. [ ] `agent/src/events.ts`：导出 `extractText`（供 worker 复用，避免两处判据漂移）
2. [ ] `agent/src/events.ts`：`mapSessionEvent` 的 `message_end` 分支判据由
   「`text.length === 0 && hasToolCall`」收紧为「`text.length === 0`」；删除 `hasToolCall`
   与那段解释注释，改为说明「空回答没有可展示内容，工具调用由 tool.execution.* 上报」
3. [ ] `agent/src/worker.ts`：新增 `lastAssistantText` 追踪（与 `lastAssistantError` 并列），
   判据 `stopReason === "stop" || "length"`（**不要**改成 `!== "error"`，否则工具 turn 正文会冒充最终回答）
   - 订阅回调的 `raw` 类型需补 `content?: unknown`（现有 cast 未声明该字段）
4. [ ] `agent/src/worker.ts`：在 `lastAssistantError` 分支之后、`succeeded` 之前插入
   空回答判定 → `settleRun(..., "failed", { code: "PROVIDER_EMPTY_ANSWER", message })`，
   并 `log.warn`（对齐 `PROVIDER_STREAM_ERROR` 分支的 flush → settle → log 顺序）

### B. 前端：渲染保护 + 文案

5. [ ] `frontend/src/components/agent/MessageList.vue`：气泡 `<div class="bubble">` 与
   `<span class="sr-only">{{ roleLabel }}说</span>` 同条件加
   `v-if="item.role === 'user' || item.text.length > 0"`
   - **不动**同级的 citations / cards / unavailable 块（R3 不得丢弃结构化内容）
6. [ ] `frontend/src/api/agent.ts`：`AGENT_ERROR_COPY` 在 `PROVIDER_STREAM_ERROR` 旁新增
   `PROVIDER_EMPTY_ANSWER: '模型没有返回内容，请重试或换个问法'`

### C. 测试

7. [ ] `agent/test/events.test.ts`：更新 `boundary of the filter` 用例
   （断言 `[{text:""}]` → `[]`，用例改名表达新语义）
8. [ ] `agent/test/worker.integration.test.ts`：新增「空最终回答 → failed PROVIDER_EMPTY_ANSWER
   且不产 assistant 事件」用例（用真实链路，不只单元断言）
9. [ ] `frontend/src/api/__tests__/agentErrors.spec.ts`：新增 `PROVIDER_EMPTY_ANSWER` 文案映射用例
10. [ ] `frontend/src/components/agent/__tests__/AgentPrimitives.spec.ts`（`describe('MessageList')` 区块）新增
    - 空正文 assistant 消息不渲染气泡文本
    - 空正文但带 `cardIds` 的 assistant 消息**仍渲染卡片**
    - 用户消息仍渲染气泡

### D. Spec 更新（Phase 3.3）

11. [ ] `.trellis/spec/backend/agent-runtime.md`：把「工具 turn 不产生 assistant 事件」那条的
    尾句「正文空且无工具调用的「空最终回答」不过滤（独立缺陷，另行处理）」改为已修复的新契约：
    **正文为空的 assistant 消息一律不产事件**，且**空最终回答结算 `failed` + `PROVIDER_EMPTY_ANSWER`**
    （R1/R2 必须同批发布的理由也要写进去）
12. [ ] 如需：在 `frontend/` 侧 spec 记录「空正文 assistant 消息不渲染气泡、但保留结构化卡片引用」

## 验证命令

```bash
cd agent && npm run lint && npm run type-check && npm test && npm run build
cd frontend && npm run lint && npm run type-check && npm test && npm run build
```

后端零改动，部署前跑受影响面确认无意外耦合：

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest -q
```

端到端（真实 backend + 真实 sidecar + 事件持久化 + SSE）：

```bash
cd backend && ./.venv/bin/python ../scripts/smoke/run_agent_memory_smoke.py --report /tmp/fg-empty-answer-smoke.json
```

## 部署（服务器）

```bash
git push origin main
ssh ubuntu@lyston 'cd /home/ubuntu/projects/FamilyGraph && git pull && systemctl --user restart familygraph-api'
ssh ubuntu@lyston 'systemctl --user restart familygraph-agent'
```

**sidecar 必须重启**（`agent/dist/main.js` 是构建产物）。另需重启**本地** sidecar
（`node dist/main.js`，经 SSH 隧道轮询远端 backend，会用旧 dist 抢到远端 job）：

```bash
cd agent && npm run build
# 本地 sidecar 按 scripts/dev-up.sh 的方式重启（nohup node dist/main.js）
```

验证：对部署后的构建产物做行为探针——`stopReason:"stop"` 且无正文 → 不产 `message.assistant_added`。

## 回滚点

- sidecar 事件过滤 + 结算判定（`events.ts`/`worker.ts`）一个 commit —— **必须整体 revert**
- 前端渲染保护 + 文案一个 commit（可独立 revert，回退为显示空气泡）
- 无 schema 变更、无数据迁移

## 明确不做（design.md §5）

不改 `length` 截断语义、不加后端 settle 不变式、不做一键重发、不清理生产遗留空行。
