# RAG 来源类别按消费者显式声明

## Goal

消除 `knowledge_retrieval.CONTEXT_SOURCE_TYPES = None` 这一「读全部已声明来源」的默认全开，
改为**集中式、按消费者显式枚举**的来源策略；任何消费者漏声明来源时 fail closed，而不是
静默拿到最宽授权。

## Background / 已核实的事实

- `knowledge_retrieval.CONTEXT_SOURCE_TYPES: tuple[str, ...] | None = None`，`None` 传给
  `search_rag` 后等价于 `_ELIGIBILITY_SQL` 里不追加 `source_type IN (...)` 条件，即**读全部
  已声明类别**。
- 三个调用点复用它：`context_builder.py:284`、`memory_rag.py:2709`、`rag.py:42`。
- **`context_builder.ContextBuilder.build(agent_kind=...)` 同时服务 assistant 与 steward**
  （`context_builder.py:195` 有 `if agent_kind == "steward" and run_id is not None:` 分支），
  且 `internal_agent.py:794` 传的是 `agent_session.agent_kind`。但来源集恒为
  `CONTEXT_SOURCE_TYPES`（全开），**与 agent_kind 无关**。
- 因此 steward 的上下文构建可以读到 `authorized_document`——该类别由 grant 分支把关
  （`_ELIGIBILITY_SQL` 的 grant 存在性检查），**没有 `:is_assistant` 门**。这比 steward
  自己的工具策略 `("memory",)` 更宽，是真实的授权不一致，不只是「缺一个显式声明」。
- `rag.py` 的 `/rag/search`（`api/memory.py:287`）是浏览器端点，复用的是「assistant 上下文」
  的常量，属于**跨消费者复用同一常量**这一类缺陷。
- 现有两个显式元组：`agent_tools.MEMORY_TOOL_SOURCE_TYPES = ("memory", "authorized_document")`
  与 `steward_memory.STEWARD_READABLE_SOURCE_TYPES = ("memory",)`。它们是对的，但分散在
  两个模块，且**与上下文路径的值不一致**。
- `models/rag.py` 已拥有 `RAG_SOURCE_TYPES` / `INDEXED_SOURCE_TYPES` /
  `UNINDEXED_SOURCE_TYPES`，且只依赖 `Base`（无服务层 import），是来源策略的唯一自然归属。
- `tests/test_knowledge_retrieval_entry.py` 已有结构性守卫：`search_rag(` 只允许出现在
  `memory_rag.py` 与 `knowledge_retrieval.py`，因此**消费者只能经统一入口**，不存在绕过路径。

## Requirements

### R1. 集中式消费者来源策略

在 `app/models/rag.py` 增加唯一真源：

```python
RAG_CONSUMER_SOURCE_TYPES: dict[str, tuple[str, ...]]
```

至少覆盖四类消费者，各自**显式枚举**，不从 `INDEXED_SOURCE_TYPES` 推导：

| key | 值 | 消费者 |
|---|---|---|
| `assistant_context` | `("memory", "authorized_document", "public_kinship")` | assistant run 的 ContextBuilder |
| `steward_context` | `("memory",)` | steward run 的上下文构建 |
| `assistant_memory_tool` | `("memory", "authorized_document")` | `familygraph.search_memory` |
| `steward_memory_tool` | `("memory",)` | `familygraph.steward.search_memory` |
| `browser_search` | `("memory", "authorized_document", "public_kinship")` | `GET /rag/search` |

### R2. 上下文来源按 agent_kind 派生

`context_builder.build()` 与 `memory_rag.build_context()` 必须**按 `agent_kind` 选策略**，
不再使用单一常量。steward 的上下文来源集不得宽于 steward 工具的来源集。

### R3. 删除 `CONTEXT_SOURCE_TYPES`

该常量必须消失（而不是改成某个具体元组）。理由是它的**名字**描述了某一个消费者，却被三个
不同消费者复用——保留它就会继续被复用。三个调用点各自改为按身份/消费者取策略。

### R4. 入口层 fail closed

`RetrievalIdentity.source_types` 改为**必填且非 None**（`tuple[str, ...]`），
`RetrievalOutcome.source_types` 同步去掉 `| None`；`retrieve()` 对运行时传入的 `None`
仍显式 422。这样「忘记声明」在构造期或入口期失败，而不是静默读全部。

### R5. 策略自校验（可证伪）

`models/rag.py` 提供纯函数校验器，检查：

- 每个消费者列出的类别都在 `RAG_SOURCE_TYPES` 内（防拼写错误）；
- 每个消费者列出的类别都在 `INDEXED_SOURCE_TYPES` 内（防把无写入方的类别写进策略）；
- 与 `UNINDEXED_SOURCE_TYPES` 无交集；
- 消费者 key 非空、值非空。

## Acceptance Criteria

- [ ] `CONTEXT_SOURCE_TYPES` 在生产代码与测试中均不再存在（结构性断言）。
- [ ] `RAG_CONSUMER_SOURCE_TYPES` 覆盖上表全部消费者，且值逐项与上表一致。
- [ ] steward 上下文构建的来源集等于 steward 工具的来源集；assistant 上下文构建的来源集
      等于 `assistant_context`。由测试直接断言两个 `agent_kind` 的派生结果。
- [ ] **反证**：给 `RAG_CONSUMER_SOURCE_TYPES` 注入一个未索引类别（如 `family_story`）
      或一个未声明类别（如 `profile_notes`），校验器必须失败。
- [ ] **反证**：把 `INDEXED_SOURCE_TYPES` 扩成包含 `family_story` 后，
      `assistant_context` / `steward_context` / 两个工具策略的值**逐项不变**——证明
      「加进索引集不会自动放宽任何消费者」。
- [ ] `public_kinship` 在 `assistant_context` 与 `browser_search` 中、但**不在**
      `assistant_memory_tool` 中（刻意的非对称，且被断言钉住）。
- [ ] `RetrievalIdentity(source_types=None)` 在入口层 422，不静默读全部。
- [ ] `agent_tools.MEMORY_TOOL_SOURCE_TYPES` 与
      `steward_memory.STEWARD_READABLE_SOURCE_TYPES` 成为策略表的别名，值不变，
      既有断言继续通过。
- [ ] 既有结构性守卫（`search_rag` 只从统一入口调用）保持通过。
- [ ] `ruff check` / `ruff format --check` / `mypy app` / `pytest` 全绿；spec 8.3 的
      「待决」张力改写为已解决并记录取舍。

## Scope boundaries

- 不改任何 `_ELIGIBILITY_SQL` 判据、grant 分支、visibility 阶梯或披露逻辑——本任务只把
  「谁读哪些类别」从隐式默认改为显式声明，不新增或放宽任何实际可读内容。
- 不改 `search_rag(source_types=None)` 的低层语义（它仍是「全部」）；消费者只能经统一入口，
  因此该默认在消费者面上不可达。
- 不新增 `source_type`，不接入 `family_story` / `profile` 的写入方。
- 不改前端、不改 agent sidecar、不改数据库或迁移。
- 行为必须**逐项保持不变**：assistant 上下文仍能读 `public_kinship`，`search_memory` 仍
  读不到它，steward 仍只读 `memory`。

## Notes

- 唯一的行为修正是 steward 上下文不再可能读到 `authorized_document`（收窄，不是放宽），
  这是本任务发现并修掉的真实授权不一致。
