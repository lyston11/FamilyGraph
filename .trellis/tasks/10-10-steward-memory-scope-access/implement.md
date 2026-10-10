# 实施计划

## 依赖顺序

配置层 → 检索层 → 工具层 → 上下文修正 → 管理面 → 回归。每一段都可独立验证。

## 步骤

### S1 配置层（`steward_memory`）

1. 迁移 `0060_steward_memory_scopes`：两列 `TEXT NOT NULL DEFAULT ''`；降级 drop。
2. `backend/app/services/steward_memory.py`：`normalize_scopes` / `encode_scopes` /
   `platform_scopes` / `space_scopes` / `effective_scopes` / `readable_scopes` /
   `resolve_reader`。
3. `backend/app/config.py`：`STEWARD_MEMORY_SCOPES: str = os.environ.get("STEWARD_MEMORY_SCOPES", "")`。
4. 模型加列：`models/platform_features.py`、`models/agent_provider.py`。
5. 测试 `backend/tests/test_steward_memory_scopes.py`：规范化、未知项忽略、三层交集、
   `readable_scopes` 在无 viewer 时去掉 private、默认全空。

**验证**：`pytest backend/tests/test_steward_memory_scopes.py` + 迁移往返（隔离 `DATA_DIR`）。

### S2 检索层

1. `memory_rag._ELIGIBILITY_SQL`：private 分支改 `:private_reader_account_id`；
   新增 `AND d.scope IN :allowed_scopes`。
2. `search_rag` 增参 `allowed_scopes: Sequence[str] | None = None`、
   `private_reader_account_id: int | None = None`；绑定与 `_typed_eligibility` 同步
   （SQLite 上参数类型必须显式声明，参照既有 `:now` 的做法）。
3. 向量检索分支（复用同一 eligibility）必须一并传参，否则两侧授权不一致。
4. 既有调用方全部显式传参（assistant 路径传全集 + `account.id`），避免隐式默认掩盖问题。

**验证**：`pytest backend/tests/test_memory_rag*.py backend/tests/test_rag_*.py` 全绿
（assistant 行为逐字不变）。

### S3 工具层

1. `steward_tools.py`：`TOOL_SEARCH_MEMORY = "familygraph.steward.search_memory"`，
   加入 `STEWARD_TOOL_NAMES` 与 `STEWARD_TOOL_INPUT_SCHEMAS`；分派分支调用
   `steward_memory.resolve_reader` + `memory_rag.search_rag`。
2. `agent_tools._steward_tool_specs()`：加 ToolSpec（描述含「只读」）；
   `default_allowlist` 在 `readable_scopes(...) == ∅` 时跳过。
3. sidecar `agent/src/tools.ts`：`STEWARD_TOOL_NAMES` 加该名、`TOOL_VERSIONS` 加条目、
   `createDomainTools` 加声明；`agent/test/tools.test.ts` 的 `STEWARD_CONTRACT` 同步。
4. 测试：`backend/tests/test_steward_memory_tool.py`
   - 空集不广告、空集调用被拒
   - private：terminology run 只读到 viewer 本人；用 space admin 的私有记忆反向断言读不到
   - private：空间级 kind 即使配置放开也读不到
   - household/lineage：非成员/跨空间读不到
   - 输入 schema 拒绝身份字段

**验证**：`pytest backend/tests/test_steward_memory_tool.py` + `cd agent && npm test`。

### S4 上下文修正（RAG 空跑 + 审计）

1. `_steward_run_context`：构造投影 `ContextSource`，`build(..., prefetched=(src,))`；
   `context_blocks = [s.as_data_block() for s in built.included]`。
2. 删除 `_steward_projection_blocks` 与 `context_blocks` 的二次构造（或让它只用于构造
   `ContextSource`，避免两处真源）。
3. 测试：steward 路径不调用 `search_rag`（spy/计数断言）；
   `ContextBuildItem.included` 与发送的 `context_blocks` 一致。

**验证**：`pytest backend/tests/test_steward_child_run_acceptance.py
backend/tests/test_context_builder*.py`；确认 digest 契约测试仍绿。

### S5 管理面

1. `schemas/platform_features.py` + `api/admin_platform_features.py`：读写 + source。
2. `schemas/agent.py` + `api/space_model_settings.py`：空间级读写 + effective。
3. 前端（若涉及显示）：`system-admin-frontend` 平台治理页与 `frontend` 空间设置页
   按既有 assist 开关的形状加一项；若无既有展示位则本步不做（不新建 UI）。

**验证**：相应 API 测试。

### S6 回归与交付

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
cd agent && npm run lint && npm run type-check && npm test
```

迁移往返（隔离 `DATA_DIR`，禁止指向生产库）：

```bash
DATA_DIR=<tmp> alembic upgrade head && DATA_DIR=<tmp> alembic downgrade -1 && DATA_DIR=<tmp> alembic upgrade head
```

## 未运行检查的说明义务

若因成本跳过前端构建或 smoke，需在交付说明中写明并给出理由。

## 提交

任务 worktree 内小步提交；`feat/10-10-steward-memory-scope-access` 只 commit，不 rebase/reset。
