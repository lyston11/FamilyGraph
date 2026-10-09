# 上下文分层预算、受控记忆工具与确定性重排

## Goal

把上一任务（10-09）建立的基线与安全骨架用到实处，交付三件**可独立验证**的能力：

- **P4 分层上下文预算**：任何单一来源类别都不能独占 token 预算——一段长家族故事
  不该把用户自己的记忆全部挤掉；
- **P2-b 受控记忆工具**：用户说「记住这个」时 agent 有确定性的提议通道，且**只能提议、
  不能确认**；
- **P3-a 确定性重排**：把「先到先得填满 limit」改为「收集候选 → 确定性重排 → 取 top-k」，
  修掉短词后备分支被主分支饿死的检索缺陷。

## Background

`10-09` 的 P0 基线给出量化事实（见 `research/evidence/p0-baseline.md`）：

- contract 层 12/12、quality 层 2/3、forbidden hits 0；
- `citation_precision` 仅 0.543——命中里有近一半不是期望来源；
- 唯一 quality 失败：`multi-session-story`（期望 2 条来源，top-5 只召回 1 条）。

代码证据（本任务实测）：

| 事实 | 位置 |
|---|---|
| 上下文是单一全局预算 + 整块纳入/排除 | `context_builder.py:288-310` |
| 候选 `collect` 在 `len(hits) >= limit` 时**立即停止**，后续分支不再运行 | `memory_rag.py:1440-1470` |
| 两字词走 LIKE 后备分支，`FTS_MIN_CHARS = 3` | `rag_query.py`、`rag_search_provider.py` |
| 工具注册表无任何记忆写路径 | `agent_tools.py:163-285` |

## Scope

- `context_builder.py` + `rag_budget.py`：按来源类别的分层预算，理由可审计。
- `agent_tools.py` + `agent_query`/`memory_rag`：`propose_memory` 与 `search_memory` 两个
  `required_kind="assistant"` 工具。
- `memory_rag.search_rag`：候选收集与确定性重排（`rank_version`）。

## Non-goals

- 不改授权模型、不加可见性来源、不绕过 `ContextBuilder` 或 provider gateway。
- 不引入向量重排（依赖 PG 迁移，见 P3-b）。
- 不做跨文档重排或模型 rerank：先做纯确定性、可复现的版本。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| AC-1 | 单一类别不能占满预算：存在用例证明长家族故事不会挤掉记忆命中。 |
| AC-2 | 分层预算进入 `policy_json` 且版本化；`_replay` 路径与首次构建一致。 |
| AC-3 | `propose_memory` 只能写 pending 候选，且必须能通过「候选仍需用户确认」的断言。 |
| AC-4 | `search_memory` 的授权与 `search_rag` 等价（同一 eligibility），不是新的检索路径。 |
| AC-5 | 重排后 quality 层提升、contract 层不回归、forbidden hits 仍为 0。 |
| AC-6 | 重排是版本化的，且旧顺序可显式回退。 |
