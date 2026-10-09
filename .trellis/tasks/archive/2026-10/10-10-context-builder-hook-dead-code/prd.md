# 清理无调用者的 context_builder.context_hook 死代码

## Goal

删除 `backend/app/services/context_builder.py` 中的 `context_hook` 函数及其 `__all__` 条目，
并删除 `backend/tests/test_context_tier_budget.py` 中为它写的
`test_context_hook_matches_builder_allocation` 测试。

## Background

- `context_builder.context_hook` 是无分层（tier）逻辑的单预算贪心（约 17 行）。
- **无任何生产调用方**：`backend/app/api/internal_agent.py` 调的是
  `policy_guard.context_hook`（安全过滤，不做预算），与它是两个不同的函数。
  `rg` 全仓扫描确认 `context_builder.context_hook` 只出现在自身定义、`__all__`
  和 `test_context_tier_budget.py` 里。
- 它的死测试 `test_context_hook_matches_builder_allocation` 断言「hook 与 builder 给出
  同一纳入集合」，但只覆盖「4 条短记忆在 2000 预算下两者都全收」的情形。
  **当 tier 生效时两者必然不一致**（builder 有 tier 门禁、hook 没有），
  该断言因此是虚假的——它给人一种「热路径与 builder 一致」的安全感，实际什么也证明不了。

## Requirements

- 删除 `context_builder.context_hook` 函数与 `__all__` 中的对应条目。
- 删除 `test_context_tier_budget.py` 中的 `test_context_hook_matches_builder_allocation`。
- 不改 `policy_guard.context_hook`（那是另一函数、仍在生产路径上）。
- 不改 `ContextBuilder.build` 的分层预算逻辑。

## Acceptance Criteria

- [ ] `rg -n "context_hook" backend --glob '*.py'` 不再出现 `context_builder.context_hook` 相关行
      （`policy_guard` 的同名函数保留）。
- [ ] `test_context_tier_budget.py` 不再引用 `context_hook`，其余 tier 测试原样保留。
- [ ] `cd backend && ruff check . && ruff format --check . && mypy app && pytest` 通过。

## Constraints

- 这是纯删除：不得顺带「修复」其它问题或重构相邻代码。
- 若删除后 `Iterable` / `MAX_INCLUDED_SOURCES` / `estimate_context` 等 import 变成未使用，
      一并清理（这是删除的直接结果，不算超范围）。
