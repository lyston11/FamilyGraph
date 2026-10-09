# PostgreSQL 触发器可移植性（Phase A 尾项）

## Goal

把 `10-05-migration-proof-gates` 盘点的 **69 个 SQLite 触发器**分类、核对 PG 等价物，
实现缺失部分，用 mutation 验证每类承重。目标是让 `10-03-postgres-migration` 的
Phase A 从「trigger 语义未核对」变成「每一类触发器都有 PG 等价物或明确的
不迁移理由」。

## Background

69 个触发器分三类（证据：`10-05` 的 `trigger-inventory.json`）：

| 类别 | 数量 | 作用 | PG 语义 |
|---|---|---|---|
| **rag 相关** | 5 | `rag_chunks_fts` 同步 + `rag_documents.revision` 强制 | `rag_chunks_fts` 是 SQLite 专属虚拟表，PG 用 PGroonga 索引；revision 触发器需要 PG 等价物 |
| **immutability 守护** | 3 | 防止不该变的行被修改 | 需要 PG 等价物（`BEFORE UPDATE ... FOR EACH ROW`） |
| **Steward revision 计数器** | 61 | `sri_*`：steward_input_revisions 的插入/更新/删除计数 | SQLite 语法（`ON CONFLICT ... DO UPDATE` + `BEGIN ... END`）在 PG 上直接执行会失败 |

## Scope

- 逐类分析 69 个触发器的 SQLite 语义与 PG 等价物。
- 实现缺失的 PG 触发器（迁移或应用层等价物）。
- 用 mutation 验证：去掉某个触发器时，对应的行为必须失败。

## Non-goals

- 不重写全部历史 Alembic 迁移（`10-05` 已证明 87/87 张表可建）。
- 不引入新的 schema 设计（Phase B 是 lease/CAS，不是这里）。
- 不做数据迁移（Phase C 的事）。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| AC-1 | 69 个触发器按三类完整分类，每类有明确的 PG 等价物或不迁移理由。 |
| AC-2 | `rag_documents_revision_*` 在 PG 上有等价物，且 mutation 验证承重。 |
| AC-3 | immutability 守护在 PG 上有等价物，且 mutation 验证承重。 |
| AC-4 | Steward revision 计数器有 PG 等价物或明确的不迁移理由（PG 不支持 SQLite 语法）。 |
| AC-5 | 每类都有「去掉它必须失败」的 mutation 证据。 |
