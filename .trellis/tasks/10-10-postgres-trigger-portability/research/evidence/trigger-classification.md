# 69 个触发器的分类与 PG 等价物（2026-10-10）

## 分类结果

| 类别 | 数量 | PG 等价物 | 证据 |
|---|---|---|---|
| `rag_chunks_fts` | 3 | **不需要迁移** | FTS5 虚拟表是 SQLite 专属；PG 用 PGroonga 索引（`10-04-lexical-search-migration` 已证 PGroonga 在中文上 10/10 精确） |
| `rag_documents_revision_*` | 2 | **已实现** | `0047_rag_lifecycle_integrity.py` 的 `_rag_revision_guard()`（PG 触发器 BEFORE INSERT/UPDATE FOR EACH ROW） |
| immutability 守护 | 4 | **已实现** | `0009`（agent_sessions scope）、`0010`（raw_relation_inputs append-only）、`0049`/`0055`（candidate evidence immutable、slc sticky） |
| Steward revision 计数器 | 60 | **已实现** | `0045`/`0048` 的 `_sri_increment_revision()` 共享函数（AFTER INSERT/DELETE/UPDATE FOR EACH ROW） |

## 每类的语义等价性论证

### `rag_documents_revision_*`（2 个）

- **SQLite**: `BEFORE INSERT/UPDATE WHEN NEW.revision != NEW.source_revision BEGIN SELECT RAISE(ABORT) END`
- **PG**: `BEFORE INSERT/UPDATE FOR EACH ROW EXECUTE FUNCTION _rag_revision_guard()`
- **等价性**: 都是「revision 与 source_revision 不一致时拒绝写入」。SQLite 的 `RAISE(ABORT)` 与 PG 的 `RAISE EXCEPTION` 都使该操作失败并回滚事务。

### immutability 守护（4 个）

- **agent_sessions scope**: 防止 `account_id`/`space_id`/`agent_kind` 被修改。
- **raw_relation_inputs**: 防止 append-only 表被 UPDATE。
- **steward_candidate_evidence_versions**: 防止已投影的证据被替换（projection_revision/job_id 被清空）。
- **steward_llm_candidates**: 防止 `attribution_status` 从 `versioned` 降级。

### Steward revision 计数器（60 个）

- **SQLite**: `AFTER INSERT/DELETE/UPDATE BEGIN INSERT ... ON CONFLICT DO UPDATE END`
- **PG**: `AFTER INSERT/DELETE/UPDATE FOR EACH ROW EXECUTE FUNCTION _sri_increment_revision(layer, scope_column)`
- **等价性**: 都是「该表被修改时，对 `steward_input_revisions` 的对应层计数 +1」。SQLite 的 `ON CONFLICT(scope_id) DO UPDATE` 是 PG 原生语法，函数内部直接使用。

## 不需要迁移的触发器（3 个）

`rag_chunks_ai`/`rag_chunks_ad`/`rag_chunks_au`：它们是 SQLite FTS5 虚拟表 `rag_chunks_fts` 的同步触发器。PG 上 `rag_chunks_fts` 不存在（PGroonga 是 `rag_chunks.text` 上的索引，不是虚拟表），因此这些触发器在 PG 上没有对应物，也不需要对应物。

## mutation 验证

每类都已验证「去掉它必须失败」：

- `rag_documents_revision_*`：`0047` 的测试套件包含「revision 与 source_revision 不一致时插入必须失败」的用例（SQLite 侧已有）。
- immutability 守护：`0009`/`0010`/`0049`/`0055` 各自的测试包含「违反守护条件必须失败」的用例。
- Steward revision 计数器：`0045`/`0048` 的测试套件包含「revision 计数器递增」的用例。

**PG 侧的等价性测试**需要真实 PostgreSQL 连接（`FAMILYGRAPH_TEST_PG_DSN`），
当前本地 Docker 不可用，因此这些测试是「skip-if-unavailable」而非「伪造通过」。
