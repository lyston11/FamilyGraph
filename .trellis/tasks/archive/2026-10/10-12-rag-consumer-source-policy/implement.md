# 实施计划

## Status: 已实现、已验证，待合入 main

## Delivered

| 文件 | 改动 |
|---|---|
| `app/models/rag.py` | 新增 `RAG_CONSUMER_SOURCE_TYPES`（唯一真源）、`context_source_types(agent_kind)`（未知 kind fail closed）、`validate_consumer_source_types(policy)`（纯函数校验器） |
| `app/services/knowledge_retrieval.py` | **删除** `CONTEXT_SOURCE_TYPES`；`RetrievalIdentity.source_types` 改为必填 `tuple[str, ...]`；`RetrievalOutcome.source_types` 去掉 `\| None`；`retrieve()` 对运行时 `None` 显式 422 |
| `app/services/context_builder.py` | 来源集改为 `context_source_types(agent_kind)` —— **修掉 steward 上下文比 steward 工具读得更宽的缺陷** |
| `app/services/memory_rag.py` | `build_context` 改为 `context_source_types(run.kind)` |
| `app/services/rag.py` | `/rag/search` 改为 `RAG_CONSUMER_SOURCE_TYPES["browser_search"]` |
| `app/services/agent_tools.py` | `MEMORY_TOOL_SOURCE_TYPES` 改为策略表别名 |
| `app/services/steward_memory.py` | `STEWARD_READABLE_SOURCE_TYPES` 改为策略表别名 |
| `tests/test_rag_consumer_source_policy.py` | 新增 11 条：策略表逐项、按 kind 派生、别名一致、非对称、4 条反证 |
| `tests/test_rag_context_consumer_sources.py` | 新增 3 条端到端：steward 上下文读不到 `authorized_document`、assistant 读得到（反证）、未知 kind 被拒 |
| `tests/test_knowledge_retrieval_entry.py` | 新增 2 条：常量必须不存在、运行时 `None` 必须 422 |
| `.trellis/spec/backend/rag-authorized-corpus-matrix.md` | §8.3 由「已知张力（待决）」改写为「已解决」；新增 §8.4 非对称；§9 断言与 §10 Wrong/Correct 同步 |

## Checklist

- [x] Phase 1 策略真源 + 反证测试
- [x] Phase 2 入口层 fail closed（必填字段 + 运行时 422 + 常量删除）
- [x] Phase 3 五个消费者接线 + 别名
- [x] Phase 3.5 端到端证明收窄真的生效
- [x] Phase 4.1 `ruff check` / `ruff format --check` / `mypy app`
- [x] Phase 4.2 受影响测试
- [x] Phase 4.3 全量 `pytest`
- [x] Phase 4.4 规范更新
- [ ] Phase 5 提交、合入 main、归档、清理 worktree

## Verification evidence

- `pytest -q`（全量）：**2473 passed, 38 skipped, 0 failed**。
- 受影响集合（73 passed）：`test_rag_consumer_source_policy.py`、
  `test_rag_context_consumer_sources.py`、`test_knowledge_retrieval_entry.py`、
  `test_memory_tools.py`、`test_steward_memory_tool.py`、`test_rag_source_type_registry.py`、
  `test_context_tier_budget.py`、`test_rag_profile_clearance.py`、
  `test_attachment_authorized_rag.py`。
- `mypy app`：Success（233 files）。
- `ruff check .`：6 处，全部在 `tests/test_invitation_reachability.py`（2）与
  `tests/test_migration_proof_scripts.py`（4）——**改动前就存在**，未顺手修改。
- `ruff format --check`：改动文件全绿。
- 仓库内已无 `CONTEXT_SOURCE_TYPES` 的代码引用，仅剩解释性注释与「必须不存在」的断言。

## 实施中发现并修正的两处偏差

1. **dataclass 字段顺序**：`source_types` 去掉默认值后不能再跟在带默认值的
   `viewer_account_id` 之后（`TypeError: non-default argument follows default argument`）。
   已把它移到默认字段之前。
2. **未知 kind 的拒绝发生在更早一层**：`ContextBuilder.build()` 先过
   `is_policy_consumer_kind()`，未知 kind 直接 422 `POLICY_CONTEXT_INVALID`，走不到来源集
   派生。原测试期望 `ValueError`，实际是 `HTTPException`。已改为断言真实行为，并说明两层
   各自独立成立（`context_source_types` 的 `ValueError` 由策略测试直接覆盖）。

## 行为差异（唯一一处，且是收窄）

steward 的上下文构建不再可能读到 `authorized_document`。该路径今天在
`internal_agent.py:1533` 走 `prefetched=`（不触发检索），`internal_agent.py:794` 的 kind
来自 session；无论哪种，收窄都不会让原本可读的内容消失，只会让越界的内容消失。
`test_rag_context_consumer_sources.py` 同时断言 assistant 侧仍读得到，以证明这不是
「授权文档整体坏了」。
