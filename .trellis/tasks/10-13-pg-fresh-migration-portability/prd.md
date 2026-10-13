# PG 全新库迁移可移植性修复

## Goal

修复使**全新 PostgreSQL 库无法从 `alembic upgrade head` 建成**的一组 SQLite-only SQL，
并把已有的可移植性守卫扩展到 `migrations/`，防止同类缺陷再次进入。

## Background

核实 `10-03-postgres-migration` 的「迁移往返」项时，在隔离 PG 上实跑
`alembic upgrade head`，发现它**从未成功过**：

- `0008_v2_foundation.py` 用 `WHERE users.is_admin = 1`（boolean 列比整数）→
  `operator does not exist: boolean = integer`；
- `0042_memory_source_contract.py` 把 `json_extract` 写进裸 `CheckConstraint` →
  PostgreSQL 无此函数，建表本身失败。

两者在 SQLite 上完全正常，因此测试套件（只跑 SQLite 迁移）看不出来。

**重要背景（不是本轮新发现）**：`scripts/migration-proof/pg_baseline_build.py` 已明确记录
「不重放历史 Alembic」是**既有设计决定**——PG 用「审查后的 baseline + 静态导入」，
而非迁移链。因此本任务**不**追求让整条迁移链在 PG 上跑通（那需要改 45 处 `DATETIME`
等 raw DDL，风险高于收益，且不在本任务范围）。

## Requirements

- R1 修复 boolean 列与整数比较的迁移（`0008`、`0045`、`0048`）。
- R2 修复 `json_extract` 进入 CHECK 约束的迁移（`0042`），改为复用方言感知构造器。
- R3 把 `test_sql_portability.py` 的守卫扩展到 `migrations/`（原守卫只扫 `app/`，
  这正是两个缺陷得以通过的原因）。
- R4 SQLite 迁移链必须仍然可用（测试套件依赖它）。

## Acceptance Criteria

- [ ] `alembic upgrade head` 在隔离 PG 上越过 `0008` 的 boolean 比较。
- [ ] 迁移里不再有 boolean 列与整数比较。
- [ ] 迁移里不再有裸 `json_extract`。
- [ ] 守卫能捕获故意重新引入的缺陷（反证）。
- [ ] SQLite 迁移链与全量测试全绿。
