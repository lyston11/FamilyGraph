# 实施记录

## P4 分层上下文预算 —— 已完成（2026-10-10）

- [x] `rag_budget.py`：`TIER_BUDGET_VERSION`（`tiered-v2`）、`tier_budget()`、
      `SOURCE_TIER_FRACTIONS`（memory 0.6 / family_story 0.4 / authorized_document 0.4 /
      profile 0.2 / public_kinship 0.2）与 `tier_budget_policy()` 审计形状。
- [x] `context_builder.py`：纳入循环加 tier 门禁（不做容量转移），exclusion reason
      `tier_budget`；`policy_json` 记录版本与份额；`_replay` 用 `_tier_allocation`
      复算并比对，预算算法变化时以 `tier_budget_changed` 失效。
- [x] 测试 `test_context_tier_budget.py`（9 项）：长 story 不挤掉 memory（含
      「三种形状缺一个则 mutation 不暴露」的用例设计）、所有 RAG_SOURCE_TYPES 已登记、
      份额 <1、最小预算不为 0、trust 门禁先于 tier、hook 与 builder 一致。

## P2-b 受控记忆工具 —— 已完成（2026-10-10）

- [x] `familygraph.search_memory` / `familygraph.propose_memory` 注册
      （`required_kind="assistant"`，v1）；Steward 不可见。
- [x] `_dispatch` 两个分支；`search_memory` 复用 `search_rag`（同一 eligibility 与
      `_rows_to_hits`），不新增检索路径；`propose_memory` 只写 pending 候选，
      `source_quote` 取本轮原始 user 消息全文（不是模型转述）。
- [x] allowlist 门禁：记忆/检索未启用时不广告（与 web / viewer 工具同一口径）。
- [x] 测试 `test_memory_tools.py`（10 项）：只写 pending、不产生 Memory/RAGDocument、
      授权等价于 search_rag、未确认不进检索、来源必须是原始用户消息、
      Steward 被拒、未启用不广告。

## P3-a 确定性重排 —— 已完成（2026-10-10）

- [x] `search_rag`：候选收集不再在 limit 处短路（改由 `_CANDIDATE_BUDGET` 约束），
      所有分支先收集候选再统一重排。
- [x] `_term_overlap_score`（查询词与正文重叠度，按长度加权）作为重排主特征，
      替代原来只是行号的 `rank`；`rank_version` 进 `policy_json`，`lex-v1` 为显式回退。
- [x] `_segment_terms` 二元组改为「先偶数位后奇数位」，固定预算下最大化被覆盖字符——
      修掉「句尾实义词（如 `桂花`）被滑动窗口噪声挤出」的缺陷。
- [x] 基线：quality 层 2/3 → **3/3**，整体 recall 0.955 → **1.000**，citation
      precision 0.543 → 0.557，forbidden hits 保持 0。quality 层由「只记录」提升为硬门。
- [x] 测试 `test_memory_rank.py`（6 项）：重叠度胜过插入顺序、lex-v1 回退、
      rank_version 校验与 trace、确定性、重叠度/分支共识特征承重（mutation）。

## 全量验证

- backend：ruff ✓ mypy ✓ pytest **2329 passed**（38 skipped 为既有环境依赖）。
- 前端：未改动（本次纯 backend）。
