# 跨语言工具注册表 parity 门禁与 steward 工具声明

## Goal

消除 `backend/app/services/agent_tools.py` 与 `agent/src/tools.ts` 两份手工维护的工具注册表
之间的漂移，方法是建立**单一真源 + 可执行的 parity 门禁**；并修掉由该漂移已经造成的
运行时缺陷与文档漂移。

## 背景：漂移已经造成运行时故障

`familygraph.propose_memory`（`required_kind="assistant"`，@1）在后端 `REGISTRY` 中存在，
但 `agent/src/tools.ts` 的 `TOOL_VERSIONS` 与 `createDomainTools()` 里**没有**它。

后果链条（每一步都是既有代码的既有行为，不需要推测）：

1. `agent_tools.default_allowlist("assistant", db, ...)` 在
   `platform_features.is_memory_enabled(db)` 为真时把 `propose_memory` 放进 allowlist
   （`agent_tools.py` 的 assistant 分支）；
2. 该 allowlist 经 run 记录 → token claim → context projection 下发；
3. `agent/src/session.ts` 的 `buildRunSession` 校验
   `projection.tool_allowlist.every((name) => allowedForKind.includes(name))`，
   其中 `allowedForKind = adapterFor("assistant").toolNames()`
   = `Object.keys(TOOL_VERSIONS)` 去掉 `familygraph.steward.*`；
4. `propose_memory` 不在其中 → **抛错**，run 在任何模型请求之前失败。

即：**记忆功能开启时，每一次 assistant run 都会失败。** `MEMORY_ENABLED=1` 是
`deploy/production/familygraph-prod.env.example` 与全部 memory smoke 脚本的配置。
`search_memory` 是同一形态（同一次提交引入，同样只有后端侧）。

引入提交：`2f758594 feat(memory): 分层上下文预算、受控记忆工具与确定性重排`
（只改 `backend/`，sidecar 侧从未补齐）。

## 背景：同源的第二类漂移（约束不同步）

`validate_input` 的子集校验器**静默忽略**它不认识的 schema 关键字，因此两侧声明
同一工具时约束可以不同而无人发现。实测差异（去掉 description 后逐项比对）：

| 工具 | 差异 |
|---|---|
| `familygraph.echo` | `text.maxLength=1000` 只在 Python |
| `familygraph.fetch_approved_page` | `approved_token.minLength=20` 只在 TS |
| `familygraph.get_term_alternatives` | `concept_code.minLength=1`、`limit.minimum/maximum=1..10` 只在 TS |
| `familygraph.list_visible_people` | `query.maxLength=50` 只在 Python |
| `familygraph.record_term_usage` | `concept_code/term.minLength=1` 只在 TS |
| `familygraph.resolve_free_text_relation` | `text.minLength=1` 只在 TS |
| `familygraph.search_space` | `query.maxLength=64` 只在 Python |
| `familygraph.search_web` | `query/use_case.minLength=1`、`limit.minimum/maximum=1..50` 只在 TS |
| `familygraph.search_memory` | 无 TS 声明 |
| `familygraph.propose_memory` | 无 TS 声明 |
| steward 全部 7 个工具 | TS 侧 `minLength/minimum/maximum/maxItems` 若干只在 TS |

两个方向都是缺陷：TS 声明是**模型看到的**，Python schema 是**服务端强制的**。
TS 比 Python 宽 = 广告一个服务端会拒的能力（spec `steward-child-run.md` §17 已把这条
写成合同：**不得向模型广告它无法使用的能力**，代价是实测 476 次白耗调用）；
TS 比 Python 窄 = 模型无法表达服务端本可受理的调用。

## 背景：文档漂移（第三类）

`agent/src/adapters/kind.ts` 对 steward 的注释写：

> the steward's set is empty by design (its output is a structured product, not tool calls)

但 `toolNamesFor("steward")` 返回 7 个工具，`default_allowlist` 也确实下发它们。

## In scope

1. **单一真源**：后端 `REGISTRY` 是工具名字、版本、`required_kind` 与
   **结构性 schema**（properties / required / type / 各类边界）的唯一真源。
2. **导出与快照**：`scripts/export-agent-tool-contract.py` 从 `REGISTRY` 导出规范化契约到
   `shared/agent-tool-contract.json`（`shared/` 是本仓库既有的跨端共享目录）。
3. **后端门禁**：新增测试重新生成契约并与快照比对——漂移即失败。
4. **侧车门禁**：新增 TS 测试读同一快照，断言
   `TOOL_VERSIONS` 与 `createDomainTools()` 的声明在名字、版本、结构 schema 上与快照逐项相等。
5. **修掉上面三类漂移**：补齐 `propose_memory` / `search_memory` 的 TS 声明；
   把两侧不一致的约束对齐（取**更严**的一侧，因为 Python 是强制方、TS 是广告方）；
   修正 `kind.ts` 注释。
6. **把 steward 工具与 prompt 的关系写成显式决定并钉住**（见 D5）。
7. 规范更新：`agent-runtime.md`（双侧 schema 同步一条升级为可执行门禁）、
   `steward-child-run.md`（steward 工具与 prompt 关系）。

## Out of scope

- 不改变任何工具的**授权**语义（allowlist 的 kind/scope/config 门禁、`required_kind`、
  viewer claim、记忆 scope 交集一律不动）；
- 不改变 `_PROMPTS` 文本（改了会移动 `prompt_version()`，见 D5）；
- 不从 Python 生成 TS 的 `description`（模型可见的中文描述仍由 TS 手工维护，属**非契约**内容）；
- 不新增/删除任何工具，不新增/删除 `source_type`；
- 不改 `steward_tools.py` 的读取分派。

## Required deliverables

- [ ] `agent_tools.canonical_contract()` 与 `_schema_contract_view()`（去掉 description、补默认值、稳定排序）
- [ ] `validate_input` 拒绝它不认识的 schema 关键字（防止"静默忽略"再成为漂移温床）
- [ ] `scripts/export-agent-tool-contract.py`（可重复运行，输出稳定）
- [ ] `shared/agent-tool-contract.json`
- [ ] `backend/tests/test_agent_tool_registry_contract.py`
- [ ] `agent/test/tool-contract-parity.test.ts`
- [ ] `agent/src/tools.ts` 补齐 `propose_memory` / `search_memory` 声明并同步边界
- [ ] `agent/src/adapters/kind.ts` 注释修正 + steward 工具/prompt 关系测试
- [ ] 两侧约束对齐，parity 门禁全绿
- [ ] 规范更新

## Acceptance criteria

- [ ] **AC1**：记忆开启时 assistant run 不再因 allowlist 校验失败。反证：把
  `propose_memory` 从 TS 声明中移除，`agent/test/tool-contract-parity.test.ts` 必须失败。
- [ ] **AC2**：`agent/test/tool-contract-parity.test.ts` 断言两侧名字集合相等（无任一方向的多余项）。
- [ ] **AC3**：同一测试断言版本号相等、结构 schema（去掉 description）逐项相等。
- [ ] **AC4**：`backend/tests/test_agent_tool_registry_contract.py` 在 `REGISTRY` 被改动而
  快照未重生成时失败。反证：改一个 `maxLength` 后不重生成 → 失败。
- [ ] **AC5**：`validate_input` 对含未知关键字的 schema 报错（500 INTERNAL_ERROR，
  因为那是注册表自身配置错误，不是调用方问题）。反证：构造含 `maxItems2` 的伪造 schema → 必须失败。
- [ ] **AC6**：两侧约束逐项相等（对齐方向：取更严的一侧）。反证：把 TS 的
  `list_visible_people.query.maxLength` 去掉 → parity 失败。
- [ ] **AC7**：`kind.ts` 不再声称 steward 工具集为空，且有一条测试钉住
  "没有任何 steward prompt 文本提及工具名"（当前决定：不声明，理由见 D5）。
- [ ] **AC8**：不引入行为回退：`pytest` 中 `test_steward_tools.py`、`test_memory_tools.py`、
  `test_agent_query_tools.py`、`test_controlled_web.py`、`test_agent_tool_admission.py`、
  `test_agent_execution_admission.py` 全绿。

## Required validation

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd backend && .venv/bin/pytest tests/test_agent_tool_registry_contract.py \
    tests/test_steward_tools.py tests/test_memory_tools.py tests/test_agent_query_tools.py \
    tests/test_controlled_web.py tests/test_agent_tool_admission.py \
    tests/test_agent_execution_admission.py tests/test_steward_memory_tool.py \
    tests/test_steward_child_run_acceptance.py
cd agent && npm run type-check && npm run lint && npm test
```

环境阻塞（`agent` 侧 `npm install` 缺失等）必须如实记录，不得记为通过。
