# S3 Steward 只读工具集实施计划

## 阶段 1：合同与现状固定

1. 读取当前 registry、schema validator、token claims、Steward run fence、publication/view/term models 和 sidecar tool declarations。
2. 固定 canonical tool names、版本、输入闭合 schema、输出安全枚举及 kind allowlist。
3. 先补 registry/schema/allowlist 快照测试，确保 Assistant 集合不变、Steward 集合显式独立。

## 阶段 2：后端只读服务

1. 新建 `backend/app/services/steward_tools.py`，复用现有 Steward projection/read helpers。
2. 实现空间级、viewer 级、证据级工具，所有查询按 token `space_id` 过滤。
3. 增加统一输入/输出边界：数值上限、不可见同形结果、JSON 字节上限、禁止 ORM/原文/凭据。
4. 不新增业务写入、不新增迁移；无法证明当前发布/attempt 绑定时返回安全 unavailable。

## 阶段 3：internal execute 接线

1. 修改 `internal_agent.execute_tool`，在 user JWT rejection 后按 run kind 选择 Assistant 或 Steward authorization。
2. 让 `agent_tools.execute` 支持无 session 的 Steward scope，同时保持 Assistant 路径签名语义和审计不变。
3. Steward 继续经过 token allowlist、policy guard、取消门禁、结果 guard 和安全 audit。
4. 在 child-run 创建/lease 的 allowlist 生成点注入 Steward registry 集合，确认 token/context/sidecar 一致。

## 阶段 4：sidecar 接线

1. 在 `agent/src/tools.ts` 增加 Steward tool schemas/descriptions。
2. 让 `toolNamesFor("steward")` 返回独立集合，禁止 Assistant/Web/write 工具混入。
3. 保持 provider wire name canonical round-trip 与未知名称 fail-closed。
4. 补 adapter/client/session/tool tests。

## 阶段 5：安全与回归验证

必须运行：

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_agent_tools.py tests/test_agent_query_tools.py tests/test_agent_execution_fence.py tests/test_steward_child_run_acceptance.py
.venv/bin/ruff check app tests/test_agent_tools.py tests/test_steward_tools.py
.venv/bin/ruff format --check app tests/test_agent_tools.py tests/test_steward_tools.py
.venv/bin/mypy app

cd ../agent
npm run type-check
npm run lint
npm test
npm run build
```

按改动范围再运行 backend 全量测试；若全量被已有 flake 或环境阻塞，记录准确失败与未运行项，不将局部绿灯报告为全量通过。

重点断言：

- Assistant 工具行为/allowlist 没有变化；
- Steward token 无 session 也能执行允许的只读工具；
- 跨空间、缺 viewer、撤权、发布代漂移、attempt 不匹配均 fail closed；
- 查询工具不产生业务写入；
- 审计不含输入正文/姓名/prompt/token；
- 删除任一授权过滤或 required_kind 检查会让变异测试失败。

## 阶段 6：收尾

1. 更新 `.trellis/spec/backend/steward-child-run.md` 与 `agent-runtime.md` 的 Steward tool contract。
2. 更新任务 `check.jsonl`/`implement.jsonl`，记录精确 spec/research 引用。
3. 运行 Trellis check，提交小步 commit，最后 archive 任务并清理 worktree/branch（若使用隔离 worktree）。
