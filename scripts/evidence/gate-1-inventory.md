# Gate 0/1 Stable Inventory（自动生成）

- root: `/Users/lyston/PycharmProjects/fg-10-05-migration-proof-gates`
- branch: `feat/10-05-migration-proof-gates`
- commit: `ad486fdcaa6e17ecb6b24f4e3873ca6a793edece`
- worktree: `/Users/lyston/PycharmProjects/fg-10-05-migration-proof-gates`

## 统计

- `app_python`: **214**
- `model_python`: **35**
- `migration_python`: **59**
- `tables`: **88**
- `indexes`: **112**
- `constraints`: **105**
- `migrations`: **59**
- `triggers`: **14**
- `virtual_tables`: **1**

## 稳定 ID 规则

- `SCHEMA-TABLE-<relative path>:<line>`
- `SCHEMA-INDEX-<relative path>:<line>`
- `SCHEMA-CONSTRAINT-<relative path>:<line>`
- `SQL-<category>-<relative path>:<line>`
- `MIGRATION-<filename>`

## 证据约束

此文件只记录 L0（源码结构扫描）证据；它不能证明 PostgreSQL 运行时语义。每个条目在后续 Gate 必须升级为 L2/L3，或明确标记为阻塞。

## 分类命中摘要

### `begin_immediate`：103 条
- `backend/app/api/action_cards.py:487` `BEGIN IMMEDIATE：本入口是「读取资格判定后再写入」的多步命令，必须在写锁内`
- `backend/app/api/action_cards.py:491` `with command_transaction(db, immediate=True):`
- `backend/app/api/admin_steward.py:526` `with steward._immediate_tx(db):`
- `backend/app/commands/bindings.py:102` `with command_transaction(session, immediate=True):`
- `backend/app/commands/context.py:55` `"""驱动级 ``BEGIN IMMEDIATE``：写锁前置，消除「检查 → 插入」的竞态窗口。`
- `backend/app/commands/context.py:68` `sa_conn.exec_driver_sql("BEGIN IMMEDIATE")`
- `backend/app/commands/context.py:81` ```immediate=True`` 在事务起点取写锁，供"读取判定后再写入"的命令使用`
- `backend/app/commands/members.py:414` `with command_transaction(session, immediate=True):`
- `backend/app/commands/members.py:472` `with command_transaction(session, immediate=True):`
- `backend/app/commands/members.py:1109` `复核）。单事务（``command_transaction(immediate=True)``，与建档门禁同一并发合同）`
- `backend/app/commands/members.py:1125` `with command_transaction(session, immediate=True):`
- `backend/app/commands/ownership.py:130` `immediate=True：SQLite 写锁前置——actor 加载、transfer 状态检查与条件`
- ……其余 91 条见 `gate-1-inventory.json`
### `raw_json_extract`：16 条
- `backend/app/models/checks.py:5` `部分 CHECK 约束用 SQLite 的 JSON 函数表达（`json_extract(col, '$.k')`）。PostgreSQL`
- `backend/app/models/checks.py:13` `SQLite 的 `json_extract(col,'$.version') = 1` 是**类型敏感**的：JSON 数字 `1` 相等，`
- `backend/app/models/json_expr.py:14` `SELECT json_extract(CAST('{"space_id":5}' AS JSON), '$.space_id');  -- NULL`
- `backend/app/models/json_expr.py:19` `- SQLite：`json_extract(col, '$.k')``
- `backend/app/models/json_expr.py:62` `return f"json_extract({col}, '{element.path}')"`
- `backend/app/models/memory.py:41` `+ "AND coalesce(json_extract(source_span_json, '$.version') = 1, 0) "`
- `backend/app/models/memory.py:42` `+ "AND coalesce(json_extract(source_span_json, '$.kind') = source_kind, 0))"`
- `backend/migrations/versions/0042_memory_source_contract.py:28` `"AND coalesce(json_extract(source_span_json, '$.version') = 1, 0) "`
- `backend/migrations/versions/0042_memory_source_contract.py:29` `"AND coalesce(json_extract(source_span_json, '$.kind') = source_kind, 0))"`
- `backend/tests/test_dialect_checks.py:10` ``json_extract(col,'$.version') = 1` 是**类型敏感**的：`
- `backend/tests/test_dialect_checks.py:152` `# `json_extract(...) = 1`；PostgreSQL 保留 JSON 类型，`'true'::jsonb != '1'::jsonb`。`
- `backend/tests/test_sql_portability.py:5` ``app/` 中有 11 处用 `func.json_extract(col, "$.k")` 做运行期查询。这些**不是**建表`
- ……其余 4 条见 `gate-1-inventory.json`
### `raw_sql`：1336 条
- `backend/app/api/admin_agent_latency.py:187` `rows = db.execute(`
- `backend/app/api/admin_agent_latency.py:218` `rows = db.execute(`
- `backend/app/api/admin_agent_latency.py:321` `rows = db.execute(`
- `backend/app/api/admin_agent_latency.py:409` `runs = db.execute(`
- `backend/app/api/admin_agent_latency.py:421` `rows = db.execute(`
- `backend/app/api/internal_agent.py:690` `def run_context(run_id: int, request: Request, db: Session = Depends(get_db)) -> ContextOut:`
- `backend/app/api/internal_agent.py:701` `return _steward_run_context(db, request, run_id, claims)`
- `backend/app/api/internal_agent.py:1146` `output = agent_tools.execute(`
- `backend/app/api/internal_agent.py:1260` `def _steward_run_context(`
- `backend/app/api/kinship.py:170` `def parse_relation_text(`
- `backend/app/api/steward_inferred.py:42` `return ActorContext(`
- `backend/app/api/steward_suggestions.py:132` `return ActorContext(`
- ……其余 1324 条见 `gate-1-inventory.json`
### `sqlite_where`：4 条
- `backend/app/models/checks.py:8` `SQLAlchemy 的 `CheckConstraint` 只接受一个 SQL 表达式，**没有** `sqlite_where` /`
- `backend/app/models/indexes.py:8` `Index("uq_x", "session_id", unique=True, sqlite_where=text("status = 'active'"))`
- `backend/app/models/indexes.py:41` `调用方不需要（也不应该）再手写 `sqlite_where` / `postgresql_where`：`
- `backend/app/models/indexes.py:53` `sqlite_where=sa.text(where),`
### `postgresql_where`：4 条
- `backend/app/models/checks.py:9` ``postgresql_where` 那样的方言分派。因此这里用 `@compiles` 为方言渲染不同文本。`
- `backend/app/models/indexes.py:20` `仓库曾出现 16 处这样的索引、0 处 `postgresql_where`。修复方式不是逐处补参数`
- `backend/app/models/indexes.py:41` `调用方不需要（也不应该）再手写 `sqlite_where` / `postgresql_where`：`
- `backend/app/models/indexes.py:54` `postgresql_where=sa.text(where),`
### `sqlite_specific`：536 条
- `backend/app/api/admin_steward.py:455` `# （SQLite JSON_EXTRACT / PostgreSQL ->>），后者在 PostgreSQL 上不存在。`
- `backend/app/api/agent.py:632` `# DB 查询放线程池，避免 SQLite 往返阻塞事件循环`
- `backend/app/api/graph.py:63` `if scope == "family" and depth < 1:  # pragma: no cover - Query 已约束`
- `backend/app/api/spaces.py:403` `if space is None:  # pragma: no cover - FK 保证存在`
- `backend/app/backup.py:1` `"""备份（AD-6）：SQLite online backup API 产出一致性快照，与 uploads 一并 tar 归档。`
- `backend/app/backup.py:9` `import sqlite3`
- `backend/app/backup.py:24` `src = sqlite3.connect(str(DB_PATH))`
- `backend/app/backup.py:25` `dst = sqlite3.connect(str(snapshot_path))`
- `backend/app/backup.py:33` `check = sqlite3.connect(str(snapshot_path))`
- `backend/app/backup.py:35` `result = check.execute("PRAGMA integrity_check").fetchone()[0]`
- `backend/app/backup.py:57` `con = sqlite3.connect(str(restored_db_path))`
- `backend/app/backup.py:59` `assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"`
- ……其余 524 条见 `gate-1-inventory.json`
### `backup`：5965 条
- `backend/app/admin_recovery.py:16` `from __future__ import annotations`
- `backend/app/admin_recovery.py:18` `import argparse`
- `backend/app/admin_recovery.py:19` `import sys`
- `backend/app/admin_recovery.py:20` `from pathlib import Path`
- `backend/app/admin_recovery.py:22` `from sqlalchemy import select`
- `backend/app/admin_recovery.py:23` `from sqlalchemy.orm import Session`
- `backend/app/admin_recovery.py:25` `from app import config`
- `backend/app/admin_recovery.py:26` `from app.models.system_admin import SystemAdmin, SystemAdminAccount`
- `backend/app/admin_recovery.py:27` `from app.services import admin_auth, admin_bootstrap, audit`
- `backend/app/admin_recovery.py:28` `from app.utils import security, timeutil`
- `backend/app/admin_recovery.py:81` `from app.db import SessionLocal`
- `backend/app/api/action_cards.py:16` `from __future__ import annotations`
- ……其余 5953 条见 `gate-1-inventory.json`
