# 技术设计：跨语言工具注册表 parity

## D1. 单一真源的方向：Python `REGISTRY` 是唯一权威

契约数据流向固定为单向：

```
backend/app/services/agent_tools.py  REGISTRY  ──export──▶  shared/agent-tool-contract.json
                                                                      │
                                            ┌─────────────────────────┴──────────────────┐
                                    后端测试：重生成并比对                      TS 测试：比对 TOOL_VERSIONS
                                    （改了 REGISTRY 未重生成 → 失败）          + createDomainTools() 声明
```

**为什么不反向（TS 为真源）**：Python 侧是**强制方**——`validate_input` 是唯一真正拒绝
非法输入的地方，`required_kind`/allowlist 门禁、`execute()` 分派也都在这一侧。
以广告方（TS）为真源，会让"模型看到的"成为权威而"服务端强制的"成为派生，
漂移的后果就从"广告过多"变成"服务端接受模型没被告知的东西"。

**为什么不生成 TS 源码**：TypeBox 的 `Type.Object` 需要 TS 类型推断才能与
`ToolDefinition<typeof XSchema>` 的参数类型绑定；生成出来的 `.ts` 会丢掉这层静态检查，
把编译期保证换成运行期。比对快照既拿到一致性，又保留类型。

**为什么不选"运行时下发"**：让后端在 context projection 里下发工具声明，sidecar 动态注册，
理论上能消掉 TS 声明。但它把工具 schema 从**编译期常量**变成**运行期输入**——
一旦投影被伪造/截断，模型拿到的能力面就由外部决定。现有设计刻意让
`createDomainTools()` 是静态的、`tool_allowlist` 只做**筛选**（子集），
这个不变式必须保留。

## D2. 契约视图的规范化规则（`_schema_contract_view`）

两侧原始 schema 的形状本来就不同，直接比对会产生大量噪声差异。规范化规则：

1. **递归删除 `description`**。它只面向模型，TS 拥有全部中文描述文本，后端描述用于
   审计/管理面。两侧文案必然不同，不属于契约。
2. **`required` 缺省补 `[]`**。TypeBox 在无可选字段时不输出 `required`。
3. **`properties` 按 key 排序**；顶层按工具名排序。
4. **`additionalProperties` 是契约字段，两侧都必须为 `false`**。理由：它是"模型不能发送
   `space_id`/`account_id`/任意权限字段"这条边界在 schema 层的表达，而
   `steward-child-run.md` §6.1 已经把 `additionalProperties=false` 写成 steward 的合同。
   TS 侧不写它等于"未声明"，而"未声明"不是"允许"，也不该被读成"允许"——
   统一写成 `false` 让两侧表达同一个边界。
   （已知 TS 侧 7 个 steward schema 已经带该字段；本次把它补齐到 assistant 的 schema。
   风险与既有 steward schema 相同：OpenAI 兼容的 strict 模式本就要求该字段。）
5. **保留 `minLength`/`maxLength`/`minimum`/`maximum`/`minItems`/`maxItems`/`enum`**：
   它们是真正的受理边界，且**当前两侧不一致**（见 PRD 的差异表），必须进契约。
6. 每个工具条目含 `version`、`required_kind`、`schema`。
   **不含** `supported_versions`：它是后端单侧的版本兼容声明（`@1`/`@2` 并存），
   sidecar 只发送 `TOOL_VERSIONS` 里的当前版本，没有对应概念。让它进契约会要求 TS
   凭空声明一个它不使用的字段。

## D3. 门禁的两半，各自能独立失败

| 门禁 | 位置 | 失败条件 |
|---|---|---|
| 快照新鲜度 | `backend/tests/test_agent_tool_registry_contract.py` | `REGISTRY` 变了但 `shared/agent-tool-contract.json` 未重生成 |
| 侧车一致性 | `agent/test/tool-contract-parity.test.ts` | `TOOL_VERSIONS` / `createDomainTools()` 与快照在名字、版本、结构 schema 上不等 |

两半的必要性：只有后端那一半时，快照与 TS 可以**一起漂移**（改了 Python + 重生成快照，
但没改 TS）而全绿。只有 TS 那一半时，快照可以过期（改了 Python 但没重生成），
TS 比对一个陈旧快照仍然"一致"。

`agent/test/tool-contract-parity.test.ts` 用 `readFileSync(new URL("../../shared/...", import.meta.url))`
读取，与 `agent/test/context.test.ts` 读 `backend/tests/fixtures/rag_context_envelope_v1.json`
完全同一形态（该文件已证明 TS 测试可以读仓库另一端的见证文件）。**不启用 `resolveJsonModule`**：
`readFileSync` + `JSON.parse` 不需要改 tsconfig，也不把仓库外的文件拉进 `tsc` 的图。

## D4. `validate_input` 必须拒绝未知关键字

现在 `_validate_value` 只读它认识的键，其余**静默忽略**。这是本任务发现的第二类漂移能
长期存在的原因：Python 侧 `maxLength` 拼错成 `maxLenght` 不会报错，只是约束失效。

加一条：每个 schema 节点先检查 `set(schema) - ALLOWED_KEYWORDS` 非空即
`500 INTERNAL_ERROR`。选 500 而不是 422：这是**注册表自身配置错误**，不是调用方的输入问题，
与 `_SUPPORTED_TYPES` 未知时的既有处理同一口径（`"注册表自身配置错误：服务器内部错误而非调用方问题"`）。

`ALLOWED_KEYWORDS = {type, properties, required, additionalProperties, items,
minLength, maxLength, minimum, maximum, minItems, maxItems, enum, description}`。

## D5. steward 工具与 prompt 的关系：显式决定，不做隐式假设

**事实**（本任务核实）：

- `default_allowlist("steward", ...)` 会下发 7 个 `familygraph.steward.*` 工具（viewer 门与
  记忆门之外的部分）；`agent/src/session.ts` 用 `adapterFor("steward").toolNames()` 校验通过；
- **`_PROMPTS` 的四个 kind 中没有任何一段提到工具或要求调用工具**；
- 四个 kind 的完整输入都在 user content 里（roster/事实清单、推荐卡、术语目标、卡片证据），
  输出是封闭 JSON schema，服务端重算栅栏（`target_ref` 集合、`semantic_hash`、`allowed_terms`、
  `supporting_fact_ids`）才是授权边界；
- 唯一有实测记录的工具活动是**拒绝**：`steward-child-run.md` §17 记录 476 次 403 / 60 个 run。

**决定**：

1. **保留 7 个工具**。删掉它们是"减少 steward 能力"的独立决定，不在本任务范围；
   它们是有意义的只读投影入口，且 `get_space_snapshot` / `list_space_nodes` 是未来
   "让 steward 自己决定读什么" 的接口。
2. **不把工具说明写进 `_PROMPTS`**。理由是可核验的成本，不是偏好：
   `prompt_digest = sha256(f"{instructions}\n{user_content}")` 且
   `prompt_version() = sha256(json.dumps(_PROMPTS))`，任何文本变更都会移动
   `prompt_version()`，而它是评测报告（`test_steward_candidate_prompt_contract.py` R4）
   与 `input_hash`/发送前 fence 的稳定依据。在四个 kind 的输入都已完整的前提下，
   加工具说明只会移动版本号，不改变模型能产生的输出。
3. **把这条决定写成可检查的合同**：`backend/tests/test_steward_tool_prompt_alignment.py`
   —— `_PROMPTS` 中出现的每个 `familygraph.*` 名字必须在 `REGISTRY` 里且
   `required_kind == "steward"`。这是 `agent/test/prompt.test.ts` 对 assistant prompt
   所做检查的镜像（那里断言 prompt 里每个 `familygraph.*` 都在 `TOOL_VERSIONS` 内）。
   **不是**冻结文本，而是"prompt 若提到工具，必须提到该 kind 真有的工具"。
4. **修正 `kind.ts` 的注释**，把"steward 工具集为空"改成实际语义：steward 的
   `emptyToolAllowlistIsInvalid = false` 是因为 allowlist 可以为空（四个 kind 都不依赖工具），
   不是因为工具集为空。

## D6. 约束对齐的方向：取更严的一侧

每一处不一致都取**更严**（更窄）的一侧，然后两侧都写它。逐项：

| 工具 | 对齐结果 | 理由 |
|---|---|---|
| `echo.text` | `maxLength=1000`（TS 补） | Python 已强制；不补则 TS 广告可发更长 |
| `fetch_approved_page.approved_token` | `minLength=20, maxLength=256`（Python 补） | TS 已声明 20；后端缺 minLength 会受理短 token |
| `get_term_alternatives` | `concept_code: minLength=1, maxLength=128`；`limit: minimum=1, maximum=10`（Python 补边界） | 后端 `execute()` 已手写 `1..10` 校验；把约束前移到 schema 让两处一致 |
| `list_visible_people.query` | `maxLength=50`（TS 补） | Python 已强制 |
| `record_term_usage` | `concept_code/term: minLength=1`（Python 补） | 空串应被 schema 拒而不是进业务层 |
| `resolve_free_text_relation.text` | `minLength=1`（Python 补） | 同上 |
| `search_space.query` | `maxLength=64`（TS 补） | Python 已强制 |
| `search_web` | `query: minLength=1, maxLength=500`；`use_case: minLength=1, maxLength=32`；`limit: minimum=1, maximum=50`（Python 补 min/bounds） | 与 TS 一致 |
| `search_memory` | TS 新增声明：`query: maxLength=500`（Python 的 `maxLength=500`）+ `limit` 边界 | 见下 |
| `propose_memory` | TS 新增声明：`summary: maxLength=2000`、`purpose: maxLength=120` | 同上 |
| steward 7 个 | TS 的 `minLength/minimum/maximum/maxItems` 补进 Python schema | TS 声明更严；后端 `execute()` 内已有等价手写校验 |

**关于 `search_memory.limit` 的 `1..20`**：后端 `execute()` 手写 `1 <= limit <= 20`，而 TS 侧
（新声明）与 Python schema 都没有边界。把 `minimum=1, maximum=20` 写进两侧 schema，
保留 `execute()` 的手写校验作为第二道（它同时负责 `isinstance(limit, bool)` 这类
schema 表达不了的判断）。`get_term_alternatives.limit` 同理（`1..10`）。

**注意**：`search_memory` 的 Python schema 目前只有 `query.maxLength=500`，没有 `limit` 边界；
补上后 `test_search_memory_rejects_empty_and_out_of_range`（断言 `limit=99` → 
`AGENT_TOOL_SCHEMA_INVALID`）的**错误码不变**，只是拒绝从 `execute()` 提前到 `validate_input()`。
两处都产出同一个 code，测试不需要改。

## D7. 新增的两个 TS 声明：实现方式

`propose_memory` / `search_memory` 的 TS 声明必须与其余工具同形态（`queryViaExecutor` 路径、
`providerWireName` 映射、`toolNamesFor("assistant")` 覆盖）。它们由
`toolNamesFor` 的**减法**自动包含（`keys(TOOL_VERSIONS)` 去掉 `familygraph.steward.*`），
所以不需要改 `toolNamesFor`，只需要：

1. `TOOL_VERSIONS` 加两行；
2. 两个 TypeBox schema；
3. `createDomainTools()` 里两个 `ToolDefinition`，走 `queryViaExecutor`。

**版本号取 `TOOL_VERSIONS[name]`**（即 1），与 `queryViaExecutor` 既有行为一致。
`supported_versions` 是后端单侧概念（D2 第 6 条），TS 不声明。

## D8. 兼容性、部署顺序与回滚

- **无数据库变更、无迁移、无 API 契约变更、无 prompt 文本变更**（D5 明确不改 `_PROMPTS`），
  因此 `prompt_version()` 不变、`prompt_digest` 语义不变。
- **部署顺序无约束**：两侧改动都只影响"sidecar 是否接受服务端下发的 allowlist"。
  - 先部署后端：`propose_memory` 进 allowlist 的行为不变（本来就有），sidecar 未更新时
    run 仍会失败——即**修复前状态**，不是新引入的失败。
  - 先部署 sidecar：TS 多了两个声明，`toolNamesFor("assistant")` 变宽，
    能接受后端下发的 `propose_memory`；后端未更新时行为不变。
  - 因此**推荐先 sidecar 后后端**，让修复在两侧都到位前就生效（后端本来就是对的）。
- **回滚**：单次 revert 即可。回滚 sidecar 会回到"记忆开启时 assistant run 失败"的
  既有缺陷状态，所以回滚必须与"关闭平台记忆开关"同时做，或回滚整个提交。
- **影响面**：`propose_memory`/`search_memory` 进入 sidecar 后，模型**可以看到并调用**它们。
  这是修复的目的——这两个工具是 P2-b 已交付的能力，后端与测试都已就位，
  缺的只是模型侧的声明。授权门禁（allowlist、`required_kind`、记忆/检索开关、
  `private` 只对本账号可读）全部在服务端，不因声明补齐而放宽。

## D9. 规范更新

- `agent-runtime.md` §8 的"双侧 schema 同步"一条：从"新增字段时两侧同改并加快照测试"
  升级为指向本任务建立的可执行门禁（`shared/agent-tool-contract.json` +
  两侧测试），并写明"未知 schema 关键字会被 500 拒绝"。
- `steward-child-run.md`：§6.1 补一句工具集与 prompt 的关系（D5 的决定 1–3），
  §8.2 的 `emptyToolAllowlistIsInvalid` 语义澄清（D5 决定 4）。
