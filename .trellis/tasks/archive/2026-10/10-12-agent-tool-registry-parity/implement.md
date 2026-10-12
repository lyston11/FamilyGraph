# 实施计划：跨语言工具注册表 parity

按顺序执行；每个切片独立可验证、独立提交。切片 1–3 是"先有门禁"，4–6 是"再修漂移"，
顺序不能颠倒：先修后建门禁会让"修到什么程度"无法证明。

## Slice 0：基线证据（不提交）

记录**修复前**的失败证据，作为后续 AC 的反证基准：

```bash
cd backend && .venv/bin/python -c "
from app.services import agent_tools as at
al = at.default_allowlist('assistant')
print('propose_memory in allowlist:', at.TOOL_PROPOSE_MEMORY in al)
"
cd agent && npx vitest --run test/adapters-kind.test.ts   # 记录当前全绿
```

用一次性探针脚本确认 `adapterFor("assistant").toolNames()` 不含 `search_memory` /
`propose_memory`（探针用完即删，不入库）。

## Slice 1：契约视图 + 导出脚本 + 快照

**文件**：`backend/app/services/agent_tools.py`、`scripts/export-agent-tool-contract.py`、
`shared/agent-tool-contract.json`

1. `agent_tools.py` 加：
   - `_SCHEMA_ALLOWED_KEYWORDS: frozenset[str]`（D4 的集合）；
   - `_schema_contract_view(schema) -> dict`（D2 的规范化：递归去 `description`、
     补 `required: []`、properties 排序、保留边界关键字）；
   - `canonical_contract() -> dict[str, dict]`（按工具名排序，条目含
     `version` / `required_kind` / `schema`）；
   - `CONTRACT_SCHEMA_VERSION = 1`（快照格式版本，将来改规范化规则时用它区分）。
2. `scripts/export-agent-tool-contract.py`：`--check` 模式（比对，不写）与默认写模式。
   输出必须**可重复**（`json.dumps(..., ensure_ascii=False, indent=1, sort_keys=True)` + 尾换行）。
   脚本用 `backend/.venv/bin/python` 可跑；不引入新依赖。
3. 生成 `shared/agent-tool-contract.json`。

**验证**：连跑两次 `export`，`git diff` 为空（幂等）。

## Slice 2：后端门禁测试

**文件**：`backend/tests/test_agent_tool_registry_contract.py`

- `test_snapshot_is_fresh`：`canonical_contract()` == 快照内容。失败信息必须指出
  哪个工具、哪个字段（便于直接重生成）。
- `test_unknown_schema_keyword_is_rejected`（AC5）：构造含 `maxLenght` 的伪造 `ToolSpec`
  传给 `validate_input`，断言 `ToolProtocolError` 且 `status_code == 500`。
  **反证**：把 `_SCHEMA_ALLOWED_KEYWORDS` 检查去掉后此用例必须失败（在实现里注明）。
- `test_every_registered_tool_appears_in_contract`：防止"注册表有工具但导出漏掉"。
- `test_contract_contains_no_description`：D2 第 1 条的可执行形式。

**验证**：`pytest tests/test_agent_tool_registry_contract.py` 全绿；故意改一个
`maxLength` 后不重生成 → `test_snapshot_is_fresh` 失败；恢复。

## Slice 3：TS 侧 parity 测试（此时预期失败）

**文件**：`agent/test/tool-contract-parity.test.ts`

- `readFileSync(new URL("../../shared/agent-tool-contract.json", import.meta.url))`。
- 断言一：`Object.keys(TOOL_VERSIONS)` 的集合 == 快照工具名集合。
- 断言二：每个工具的 `TOOL_VERSIONS[name] === contract[name].version`。
- 断言三：`createDomainTools(stub)` 里每个工具的 `parameters` 经**同一套规范化**
  （TS 侧实现一份等价的 `contractView`）后 == `contract[name].schema`。
  TS 侧的规范化必须与 Python 侧逐条对应；两者不一致本身也要能被发现
  （断言四：快照里每个 schema 顶层都有 `additionalProperties: false`）。
- 断言五：`adapterFor("assistant").toolNames()` ∪ `adapterFor("steward").toolNames()`
  == 快照工具名集合，且两个集合不相交（把 `toolNamesFor` 也纳入门禁）。

**此时预期失败**（`search_memory` / `propose_memory` 缺失、若干边界不一致）——
**记录失败输出**作为 Slice 4–6 的驱动证据。

## Slice 4：补齐两个缺失的 TS 声明（AC1、AC2）

**文件**：`agent/src/tools.ts`

- `TOOL_VERSIONS` 加 `"familygraph.search_memory": 1`、`"familygraph.propose_memory": 1`；
- 两个 TypeBox schema（`SearchMemorySchema`、`ProposeMemorySchema`），
  `additionalProperties: false`，字段与后端逐项一致（含 Slice 6 的边界）；
- `createDomainTools()` 里两个 `ToolDefinition`，走 `queryViaExecutor`；
- 描述文本用后端 `REGISTRY` 的中文描述为底稿（D2 说明：description 不进契约，
  但文案应与后端语义一致）。

**验证**：parity 测试的断言一、二、五通过。

## Slice 5：修正 `kind.ts` 注释与 steward 工具/prompt 合同（AC7）

**文件**：`agent/src/adapters/kind.ts`、`backend/tests/test_steward_tool_prompt_alignment.py`、
`agent/test/adapters-kind.test.ts`

- 改 `emptyToolAllowlistIsInvalid` 的注释：从"steward 工具集为空"改为
  "allowlist 可以为空（四个 kind 的输入都在 user content 里，不依赖工具调用）"。
- 新测试 `test_steward_tool_prompt_alignment.py`：`_PROMPTS` 中每个 `familygraph.*`
  名字必须在 `REGISTRY` 且 `required_kind == "steward"`；
  并断言**当前**四个 kind 都不提工具（把 D5 的决定 2 钉住，将来要加工具说明就必须
  同时更新这条测试与 `prompt_version` 基线）。
- `adapters-kind.test.ts` 补一条：steward 的 `toolNames()` 非空（防止注释与代码再次分叉）。

## Slice 6：两侧约束对齐（AC3、AC6）

**文件**：`backend/app/services/agent_tools.py`、`agent/src/tools.ts`

按 design D6 的表逐项对齐。后端侧改动集中在 `REGISTRY` 里各 `input_schema` 字面量；
`execute()` 的手写边界校验**保留**（第二道，且负责 schema 表达不了的判断）。

**验证**：
```bash
cd backend && .venv/bin/python ../scripts/export-agent-tool-contract.py
cd backend && .venv/bin/pytest tests/test_memory_tools.py tests/test_agent_query_tools.py \
    tests/test_steward_tools.py tests/test_controlled_web.py
cd agent && npm test
```

## Slice 7：规范更新

**文件**：`.trellis/spec/backend/agent-runtime.md`、`.trellis/spec/backend/steward-child-run.md`

按 design D9 更新两处。`steward-child-run.md` 的 §6.1 与 §8.2 各加一段；
`agent-runtime.md` §8 的"双侧 schema 同步"一条改为指向新门禁。

## Slice 8：全量验证与提交

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd backend && .venv/bin/pytest tests/test_agent_tool_registry_contract.py \
    tests/test_steward_tools.py tests/test_memory_tools.py tests/test_agent_query_tools.py \
    tests/test_controlled_web.py tests/test_agent_tool_admission.py \
    tests/test_agent_execution_admission.py tests/test_steward_memory_tool.py \
    tests/test_steward_child_run_acceptance.py tests/test_steward_tool_prompt_alignment.py \
    tests/test_rag_consumer_source_policy.py
cd agent && npm run type-check && npm run lint && npm test
```

`scripts/smoke/run_agent_memory_smoke.py` 是**端到端反证**：它在 `MEMORY_ENABLED=1` 下跑
真实 sidecar，修复前必然在 `buildRunSession` 失败。环境允许时必须跑；不允许则如实记录
环境阻塞（退出码 2 不算通过）。

## Risks

- **`additionalProperties: false` 加进 assistant schema 可能被严格模式的 relay 拒绝**。
  缓解：steward 的 7 个 schema 已经带该字段且已在生产运行；先跑
  `agent` 侧测试与 `run_agent_memory_smoke.py`，若 relay 拒绝则回退到"只在快照里
  记录两侧实际值并断言它们相等"（放弃"必须为 false"这条，保留一致性门禁）。
- **`validate_input` 新增的未知关键字拒绝可能命中现有 schema**。
  缓解：Slice 2 的 `test_every_registered_tool_appears_in_contract` 会先遍历全部 schema；
  实现时先跑一次全量 `pytest tests/` 的相关子集确认没有既有 schema 用了集合外的关键字。
- **TS 侧规范化与 Python 侧必须逐条对应**。缓解：Slice 3 的断言四（顶层
  `additionalProperties`）与 Slice 6 的逐项对齐会暴露不一致；两侧规范化函数互相
  写一句指向对方的注释。

## 执行记录（2026-10-12）

全部切片完成，6 个提交（均在 `feat/10-12-agent-tool-registry-parity`）：

| 提交 | 内容 |
|---|---|
| `8a86e1f0` | 契约视图 + 导出脚本 + 快照 + 后端门禁（含未知关键字 500 拒绝） |
| `8f974139` | TS 侧 parity 门禁（当时 6 个用例全失败，驱动修复） |
| `78ebdb3c` | 补齐两个声明 + 两侧边界对齐 + `additionalProperties` 安全修正 |
| `4aacca84` | kind.ts 注释修正 + steward 工具/prompt 合同测试 |
| `d0a83b91` | 规范更新 |
| `2c97fb28` | 新增文件的行长/格式修正 |

### 计划外的设计修正（实测发现，已写进 spec）

**`additionalProperties: false` 不得加进 assistant 的 TS 声明。** 原设计 D2 第 4 条要求
两侧都声明它，实施时被 `worker.integration.test.ts` 拦下：该关键字会让 SDK 的
`validateToolArguments` 在校验期**剥掉**多余字段，而 `policy.ts` 的 scope-override 硬阻断读的
是**校验后**的参数。结果是真实的越权尝试静默消失——无 `POLICY_TOOL_BLOCKED`、无审计行、
run 反而 `succeeded`。已改为：该关键字排除出跨语言契约视图，两侧的非对称由各自断言钉住
（后端全量 schema 必须拒绝多余字段；assistant 的 TS 声明必须不带它；steward 的 7 个既有
声明保留它）。

**`supported_versions` 必须进契约。** 原设计 D2 第 6 条说它不进，实施时 parity 门禁立刻失败：
`get_relationship_path` / `explain_structural_path` 后端已 `@2`，sidecar 刻意仍发 `@1`。
版本断言因此改为「发送的版本 ∈ 兼容集」，而不是等值。

### 验证结果

- `backend`：`ruff check` / `ruff format --check` / `mypy app` 全绿（新增文件干净；仓库既有
  6 个 ruff 错误在 main 上同样存在，未触碰）；`pytest` **2485 passed, 38 skipped**。
- `agent`：`type-check` / `lint` / `test`（**241 passed, 23 files**）/ `build` 全绿。
- 反证（均已实测）：移除 TS 的 `search_memory` 声明 → parity 4 个用例失败；
  去掉 `list_visible_people.query.maxLength` → parity 失败；
  改后端 `maxLength` 不重生成快照 → 快照新鲜度失败；
  伪造含 `maxLenght` 的 schema → 500 拒绝。

### 环境阻塞（如实记录，未记为通过）

`scripts/smoke/run_agent_memory_smoke.py` 退出码 1：64 项中 30 项失败，
失败点在真实 sidecar 循环的 `provider_calls == 1`（0 !== 1）。**在 main 检出上同样失败，
且失败项集合逐项相同**，因此是既有环境/契约问题，不由本任务引入，也未在本任务修复
（不在范围内）。它本可作为 AC1 的端到端反证，因此 AC1 的证据改为：
(a) 基线探针复现 `buildRunSession` 对 `propose_memory` 抛错；
(b) 修复后 `adapterFor("assistant").toolNames()` 包含两个工具且 allowlist 校验不再抛错；
(c) parity 门禁 + 上述四条反证。

### 未做（Out of scope 已声明）

- 未改任何工具的授权语义（allowlist 门禁、`required_kind`、viewer claim、记忆 scope 交集）。
- 未改 `_PROMPTS` 文本，因此 `prompt_version()` 与 `prompt_digest` 基线未移动。
- 未修 `run_agent_memory_smoke.py` 的既有失败。
