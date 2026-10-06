# Gate 0/1 Stable Inventory（自动生成）

- root: `/Users/lyston/PycharmProjects/familygraph`
- branch: `main`
- commit: `91787daef9a639dc9abbaea60b6ab9a309459ef8`
- worktree: `/Users/lyston/PycharmProjects/familygraph`

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

### `begin_immediate`：104 条
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
- ……其余 92 条见 `gate-1-inventory.json`
### `raw_json_extract`：17 条
- `backend/.venv/lib/python3.12/site-packages/sqlalchemy/testing/requirements.py:1124` `def legacy_unconditional_json_extract(self):`
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
- ……其余 5 条见 `gate-1-inventory.json`
### `raw_sql`：3603 条
- `backend/.venv/lib/python3.12/site-packages/PIL/FontFile.py:122` `self.bitmap.save(os.path.splitext(filename)[0] + ".pbm", "PNG")`
- `backend/.venv/lib/python3.12/site-packages/PIL/FontFile.py:125` `with open(os.path.splitext(filename)[0] + ".pil", "wb") as fp:`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImImagePlugin.py:356` `name, ext = os.path.splitext(os.path.basename(filename))`
- `backend/.venv/lib/python3.12/site-packages/PIL/Image.py:709` `p.text(`
- `backend/.venv/lib/python3.12/site-packages/PIL/Image.py:2573` `filename_ext = os.path.splitext(filename)[1].lower()`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImageDraw.py:575` `def text(`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImageDraw.py:607` `return self.multiline_text(`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImageDraw.py:630` `def draw_text(ink: int, stroke_width: float = 0) -> None:`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImageDraw.py:692` `draw_text(stroke_ink, stroke_width)`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImageDraw.py:695` `draw_text(ink, 0)`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImageDraw.py:698` `draw_text(ink)`
- `backend/.venv/lib/python3.12/site-packages/PIL/ImageDraw.py:700` `def multiline_text(`
- ……其余 3591 条见 `gate-1-inventory.json`
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
### `sqlite_specific`：2346 条
- `backend/.venv/lib/python3.12/site-packages/_pytest/__init__.py:9` `except ImportError:  # pragma: no cover`
- `backend/.venv/lib/python3.12/site-packages/_pytest/compat.py:259` `except Exception:  # pragma: no cover`
- `backend/.venv/lib/python3.12/site-packages/_pytest/config/__init__.py:1592` `raise ValueError(msg, value)  # pragma: no cover`
- `backend/.venv/lib/python3.12/site-packages/_pytest/config/argparsing.py:450` `if sys.version_info < (3, 9):  # pragma: no cover`
- `backend/.venv/lib/python3.12/site-packages/_pytest/doctest.py:544` `return  # pragma: no cover`
- `backend/.venv/lib/python3.12/site-packages/_pytest/pathlib.py:926` `except ValueError:  # pragma: no cover`
- `backend/.venv/lib/python3.12/site-packages/_pytest/reports.py:210` `fail(  # pragma: no cover`
- `backend/.venv/lib/python3.12/site-packages/alembic/autogenerate/api.py:75` `engine = create_engine("sqlite://")`
- `backend/.venv/lib/python3.12/site-packages/alembic/autogenerate/compare.py:900` `def _setup_autoincrement(`
- `backend/.venv/lib/python3.12/site-packages/alembic/autogenerate/compare.py:909` `if metadata_col.table._autoincrement_column is metadata_col:`
- `backend/.venv/lib/python3.12/site-packages/alembic/autogenerate/compare.py:910` `alter_column_op.kw["autoincrement"] = True`
- `backend/.venv/lib/python3.12/site-packages/alembic/autogenerate/compare.py:911` `elif metadata_col.autoincrement is True:`
- ……其余 2334 条见 `gate-1-inventory.json`
### `backup`：37560 条
- `backend/.venv/lib/python3.12/site-packages/PIL/BdfFontFile.py:23` `from __future__ import annotations`
- `backend/.venv/lib/python3.12/site-packages/PIL/BdfFontFile.py:25` `from typing import BinaryIO`
- `backend/.venv/lib/python3.12/site-packages/PIL/BdfFontFile.py:27` `from . import FontFile, Image`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:32` `from __future__ import annotations`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:34` `import abc`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:35` `import os`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:36` `import struct`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:37` `from enum import IntEnum`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:38` `from io import BytesIO`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:39` `from typing import IO`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:41` `from . import Image, ImageFile`
- `backend/.venv/lib/python3.12/site-packages/PIL/BlpImagePlugin.py:363` `from .JpegImagePlugin import JpegImageFile`
- ……其余 37548 条见 `gate-1-inventory.json`
