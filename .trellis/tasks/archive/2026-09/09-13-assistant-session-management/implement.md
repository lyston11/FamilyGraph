# Implement：助手会话管理重做

## 执行顺序

### 后端

- [ ] 1. `models/agent.py`：`AgentSession` 增加 `title`、`updated_at` 字段。
- [ ] 2. 标题派生工具 `derive_session_title`（按仓库分层惯例落位，供 API 与迁移复用）。
- [ ] 3. 迁移 `migrations/versions/0040_agent_session_title.py`（down_revision=0039）：
       加列 + Python 回填 title/updated_at；downgrade drop。
- [ ] 4. `schemas/agent.py`：`AgentSessionOut` 增两字段；新增 `AgentSessionRenameRequest`。
- [ ] 5. `api/agent.py`：
  - create session 写 `updated_at`、投影补全；
  - `GET /sessions` 排序 + `limit`；
  - message create 正常路径刷新 `updated_at` + 首条派生 title；
  - `PATCH /sessions/{id}` 重命名；
  - `DELETE /sessions/{id}`（活跃 Run 409 + 审计）。
- [ ] 6. 后端测试（test_agent_browser_api.py 增补，覆盖 design §4 全部用例）。

### 前端

- [ ] 7. `types/agent.ts` + `api/agent.ts`：类型字段、rename/delete 封装。
- [ ] 8. `stores/agent.ts`：title 解析顺序、renameSession、deleteSession（含 active 清场与 409 error）。
- [ ] 9. `SessionList.vue` 重做（面板内展开列表，移除 NSelect）+ 相对时间格式化。
- [ ] 10. 前端测试更新：AgentPrimitives / agent store / AssistantPanel 相关断言。

## 验证命令

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
# 迁移隔离验证（临时库）：
#   DATABASE_URL=sqlite:////tmp/agent-title-test.db alembic upgrade head
cd frontend && npm run lint && npm run type-check && npm test && npm run build
```

未运行的高成本检查：`system-admin-frontend` 检查（本改动不触及管理员前端）与
`./scripts/frontend-api-smoke.sh`（需要完整环境，如交付前环境可用则补跑）。

## 回滚点

- 后端：迁移 downgrade + revert 后端提交。
- 前端：revert 前端提交（SessionList 为局部替换，无跨模块耦合）。

## Review Gate

- 提交前对照 prd.md AC-1..AC-8 逐条核对。
