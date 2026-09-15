# 助手链路优化：执行计划

> 设计见 `design.md`。**R2（会话压缩）已按核查结论放弃**，实际范围 = R1 空事件过滤 + 前端指示防御。

## 前置

- [x] 核查完成（`design.md` §0 记录 R2 证伪与 R1 实测影响）
- [ ] implement.jsonl / check.jsonl 配置 spec 上下文
- [ ] `task.py start` 建 worktree

## 实施步骤

1. [ ] `agent/src/events.ts`：`message_end` 分支加空文本 + toolCall 判据，命中返回 `[]`
2. [ ] `agent/test/events.test.ts`：新增 3 例（空+toolCall → 无事件；有正文+toolCall → 有事件；空+无 toolCall → 有事件）
3. [ ] `frontend/src/components/agent/MessageList.vue`：`showPendingIndicator` 收紧为 `m.text.length > 0`
4. [ ] `frontend/src/components/agent/__tests__/AgentPrimitives.spec.ts`：新增 2 例（空 assistant 不熄灭指示；非空照常熄灭）
5. [ ] 核对 `agent/test/worker.integration.test.ts` 的 3 处 toolUse fixture 文本非空（非空即不受影响）

## 验证命令

```bash
cd agent && npm run lint && npm run type-check
cd agent && npx vitest run test/events.test.ts test/session-history.test.ts test/worker.integration.test.ts
cd agent && npm test && npm run build

cd frontend && npm run lint && npm run type-check
cd frontend && npx vitest run src/components/agent/__tests__/AgentPrimitives.spec.ts \
  src/components/agent/__tests__/AssistantPanel.spec.ts src/stores/__tests__/agent.spec.ts
cd frontend && npm test && npm run build
```

后端零改动，无需跑 pytest；但部署前跑一次受影响面确认无意外耦合：

```bash
cd backend && pytest tests/test_agent_events*.py -q
```

## 部署（服务器）

```bash
git push origin main
ssh ubuntu@lyston 'cd /home/ubuntu/projects/FamilyGraph && git pull'
ssh ubuntu@lyston 'cd agent && npm run build'
ssh ubuntu@lyston 'systemctl --user restart familygraph-agent'
```

前端为本地 vite dev server（5173，cwd=主检出 frontend/，`/api` 经隧道到远端 8000），合并进 main 后 HMR 直接生效，无需构建同步 dist。

验证：

```bash
# 1. 新会话（含工具调用的问题）→ 事件流无空 assistant
ssh ubuntu@lyston 'cd /home/ubuntu/projects/FamilyGraph/backend && sqlite3 data/db/app.db \
  "select seq, type, json_extract(public_payload,\"\$.text\") from agent_run_events \
   where run_id=(select max(id) from agent_runs) order by seq;"'
# 期望：message.assistant_added 只出现最终回答，text 非空

# 2. 无新的空 assistant 消息行
ssh ubuntu@lyston 'cd /home/ubuntu/projects/FamilyGraph/backend && sqlite3 data/db/app.db \
  "select count(*) from agent_messages where role=\"assistant\" and length(json_extract(content_json,\"\$.text\"))=0;"'
# 期望：仅历史遗留 1 行（id=5，不回改），新 run 不新增
```

## 回滚点

- `events.ts` 过滤单独 commit（revert 后回到产空事件）
- `MessageList.vue` 指示收紧单独 commit（revert 后回到「空消息即熄灭」）

## 明确不做（design.md §5）

不新增压缩、不改历史投影、不清理历史空消息、不处理空最终回答、不做中文 token 估算校准。
