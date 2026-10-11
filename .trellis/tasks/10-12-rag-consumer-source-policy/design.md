# 技术设计：消费者来源策略

## D1. 策略表的归属：`app/models/rag.py`

选它的理由：

- 它已经拥有 `RAG_SOURCE_TYPES` / `INDEXED_SOURCE_TYPES` / `UNINDEXED_SOURCE_TYPES`，
  把「谁可以读哪些类别」放在同一处，读一个文件就能看到全部来源策略；
- 它只 import `app.models.base.Base`，**没有任何服务层依赖**，因此 `agent_tools`、
  `steward_memory`、`knowledge_retrieval`、`context_builder`、`rag` 都可以安全 import 它，
  不存在循环依赖风险；
- 备选「独立 `rag_policy.py`」会把同一主题拆成两个文件，而收益只是文件名更贴切——不划算。

## D2. 消费者键的设计：按 (agent_kind, 面) 命名，不按模块名

```python
RAG_CONSUMER_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "assistant_context":      ("memory", "authorized_document", "public_kinship"),
    "steward_context":        ("memory",),
    "assistant_memory_tool":  ("memory", "authorized_document"),
    "steward_memory_tool":    ("memory",),
    "browser_search":         ("memory", "authorized_document", "public_kinship"),
}
```

**为什么 `steward_context` 是独立的一行而不是复用 `assistant_context`。**
这是本任务发现的核心缺陷：`ContextBuilder.build()` 同时服务两种 kind，而来源集原来恒为
「全部」。`steward_context = ("memory",)` 与 `steward_memory_tool` 相同，于是
「steward 在上下文里能读的」不再宽于「steward 在工具里能读的」——这条等式本身就是可断言的
合同，比注释可靠。

**为什么 `browser_search` 独立成行而不是复用 `assistant_context`。**
`/rag/search` 是浏览器端点（用户自己问自己的空间），不是 agent 上下文。两者今天取值相同，
但**消费者不同**：将来若要调整 agent 上下文的来源面，不应连带改变用户自己看到的搜索结果。
跨消费者复用同一常量正是本任务要消除的形态。

**为什么 `public_kinship` 在上下文里、不在 `search_memory` 里。**
`search_memory` 的名字是「记忆」，返回空间外的通用称谓知识会误导模型把语料当成用户记忆
（2026-10-10 已实测到 `term-pack:zh-CN` 出现在结果里）。上下文构建则不同：它是拼装材料，
通用知识作为背景是合理的。这个非对称是刻意的，由测试钉住。

## D3. 派生：按 agent_kind 取策略，未知 kind fail closed

```python
def context_source_types(agent_kind: str) -> tuple[str, ...]:
    key = {"assistant": "assistant_context", "steward": "steward_context"}.get(agent_kind)
    if key is None:
        raise ...  # 未知 kind 不得回退到最宽集合
    return RAG_CONSUMER_SOURCE_TYPES[key]
```

放在 `models/rag.py`（纯映射，无 DB）。未知 kind 的 fail closed 与
`knowledge_retrieval.resolve_authorization` 的既有行为一致——那里也对未知 kind 抛 422。

## D4. 删除 `CONTEXT_SOURCE_TYPES`

不改成具体元组，直接删。理由：它的**名字**描述单一消费者（"context"），却被
`context_builder` / `memory_rag.build_context` / `rag.py` 三个不同消费者复用。保留一个
语义含糊的共享常量，等于把「复用错常量」这条路留着。删除后每个调用点必须写出自己是谁：

| 调用点 | 改为 |
|---|---|
| `context_builder.py` | `rag_model.context_source_types(agent_kind)` |
| `memory_rag.build_context` | `rag_model.context_source_types(run.kind)` |
| `rag.py` `/rag/search` | `RAG_CONSUMER_SOURCE_TYPES["browser_search"]` |
| `agent_tools` | `MEMORY_TOOL_SOURCE_TYPES` → 策略表别名 |
| `steward_tools` | `STEWARD_READABLE_SOURCE_TYPES` → 策略表别名 |

## D5. 入口层 fail closed

```python
@dataclass(frozen=True)
class RetrievalIdentity:
    ...
    source_types: tuple[str, ...]      # 必填，去掉默认值与 | None
```

`RetrievalOutcome.source_types` 同步改为 `tuple[str, ...]`。

`retrieve()` 仍保留运行时检查：

```python
if identity.source_types is None:
    raise_api_error(422, MEMORY_SCOPE_FORBIDDEN, "检索身份必须显式声明来源类别")
```

**为什么类型收窄之后还要留运行时检查。** 类型标注不约束运行时（未类型化调用点、反射、
`**kwargs` 组装都能绕过），而这个默认值的失效后果是「静默读全部」。宁可多一层 422。

## D6. 策略自校验：纯函数 + 反证测试

```python
def validate_consumer_source_types(
    policy: Mapping[str, Sequence[str]],
) -> list[str]:
    """返回策略错误列表；空列表表示通过。纯函数，便于用伪造策略做反证。"""
```

检查四项：key 非空、值非空、值 ⊆ `RAG_SOURCE_TYPES`、值 ⊆ `INDEXED_SOURCE_TYPES`、
值 ∩ `UNINDEXED_SOURCE_TYPES` = ∅。

**为什么不在 import 期抛异常。** 该模块被几乎所有服务 import，import 期抛错会让一次策略
笔误表现为「整个应用起不来」，且堆栈指向 import 链而不是策略行。测试期门禁同样 fail
closed（CI 会红），但诊断信息指向具体消费者与具体类别。

**反证义务。** 没有反证，校验器就只是同义反复。两条反证：

1. 伪造策略含 `family_story`（已声明但未索引）→ 校验器必须报错；
2. 伪造策略含 `profile_notes`（未声明）→ 校验器必须报错。

以及一条**承重**的反证：把 `INDEXED_SOURCE_TYPES` 扩成含 `family_story`，
断言所有消费者值逐项不变——直接证明「加进索引集不会放宽任何消费者」。

## D7. 别名而非搬移

`agent_tools.MEMORY_TOOL_SOURCE_TYPES` 与 `steward_memory.STEWARD_READABLE_SOURCE_TYPES`
保留名字（既有测试与文档引用它们），值改为读策略表：

```python
MEMORY_TOOL_SOURCE_TYPES = RAG_CONSUMER_SOURCE_TYPES["assistant_memory_tool"]
```

好处：单一真源，且调用点不必改（`agent_tools.py:943`、`steward_tools.py:562` 继续引用
本地名字）。风险是「别名可以再被赋值」——由测试断言别名与策略表相等来兜住。

## D8. 兼容性与回滚

- 无数据库变更、无迁移、无 API 契约变更。
- 行为差异只有一处：steward 上下文不再可能读到 `authorized_document`（**收窄**）。
  该路径今天在 `internal_agent.py:1533` 走 `prefetched=`，不触发检索；`internal_agent.py:794`
  是否会出现 steward kind 取决于 session，无论哪种，收窄都不会让原本可读的内容消失，
  只会让越界的内容消失。
- 回滚：单次 revert 即可，无数据影响。
