# 实施记录

## 已完成

- [x] `0008_v2_foundation.py`：`users.is_admin = 1` → `= TRUE`
- [x] `0045_steward_staged_publication.py`：`manifest_sealed = 1` → `= TRUE`
- [x] `0048_steward_terminology_publication.py`：`manifest_sealed=1` → `=TRUE`
- [x] `0042_memory_source_contract.py`：裸 `CheckConstraint` → 复用
      `app.models.memory.source_snapshot_check`（`DialectCheck`），并删除复制来的字面量
- [x] `test_sql_portability.py`：守卫扩展到 `migrations/`，新增两条迁移专用断言

## 验证

- 隔离 PG 上 `alembic upgrade head`：越过 0008 的 boolean 比较（下一处阻塞是
  `DATETIME`，属既有设计决定覆盖的范围，见 prd.md 的 Background）。
- 反证：把 `0045` 改回 `= 1`，新守卫失败；还原后通过。
- 全量 `pytest`：2490 passed / 0 failed。
- `mypy app` Success；`ruff format --check` 通过。

## 未做（刻意）

- 不改 45 处 `DATETIME` / 29 处 `PRAGMA` / `last_insert_rowid` / `sqlite_master`：
  PG baseline 方案是既有设计决定，批量改 raw DDL 风险高于收益。
- 不为迁移链加「全新 PG 从迁移建成」的 CI 断言：在 baseline 方案下它不是合同。
