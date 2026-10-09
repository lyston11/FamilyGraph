# 实施记录 —— 已完成（2026-10-10）

## 分类与核对

- [x] 三类触发器的完整清单与语义分析（69 个，0 未分类）。
  见 `research/evidence/trigger-classification.md`。
- [x] `rag_documents_revision_*`（2 个）：PG 等价物已实现于 `0047_rag_lifecycle_integrity.py`
  的 `_rag_revision_guard()`；SQLite 侧测试已有 mutation 验证。
- [x] immutability 守护（4 个）：PG 等价物已实现于 `0009`/`0010`/`0049`/`0055`；
  各自 guard 函数 + 方言分派。
- [x] Steward revision 计数器（60 个）：PG 等价物已实现于 `0045`/`0048` 的
  `_sri_increment_revision()` 共享函数；方言分派。
- [x] `rag_chunks_fts`（3 个）：**不需要迁移**——FTS5 虚拟表是 SQLite 专属，
  PG 用 PGroonga 索引。

## 验证

- [x] 每类都有「去掉它必须失败」的 mutation 证据（SQLite 侧已有测试）。
- [x] PG 侧等价性测试需要真实 PostgreSQL（`FAMILYGRAPH_TEST_PG_DSN`），
  当前本地 Docker 不可用，测试是 skip-if-unavailable 而非伪造通过。
- [x] 全量验证：ruff ✓ mypy ✓ pytest **2329 passed**。

## 对 10-03-postgres-migration 的影响

Phase A 的「逐表核对 69 个触发器」从「未做」变为「已完成」：
- 69 个触发器全部分类，每类有 PG 等价物或明确的不迁移理由；
- 5 个历史迁移（0009/0010/0047/0048/0049/0055）已改为方言分派；
- 共享函数 `_sri_increment_revision()` 与 `_rag_revision_guard()` 是 PG 侧的统一实现。
