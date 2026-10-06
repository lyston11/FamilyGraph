# Gate 1：PRAGMA 与 SQLite DDL 逐条检查

由 `scripts/migration-proof/build_pr_checks.py` 生成。

## PRAGMA

| PRAGMA | 处数 | PostgreSQL 等价物 |
|---|---|---|
| `busy_timeout` | 1 | 由 lock_timeout / statement_timeout 取代 |
| `foreign_key_check` | 1 | 由 FK 约束在写入时强制；迁移期可临时禁用后校验 pg_constraint |
| `foreign_keys` | 24 | 外键由 FK 约束本身强制，无需开关 |
| `integrity_check` | 2 | 由 pg_verify_checksums / pg_dump 校验取代 |
| `journal_mode` | 1 | WAL 由服务器配置，非连接级 |
| `synchronous` | 1 | 由服务器 fsync 配置取代 |
| `table_info` | 1 | 由 information_schema.columns 取代 |

## SQLite DDL

| 构造 | 处数 |
|---|---|
| trigger | 14 |
| trigger_body | 8 |
| virtual_table | 1 |

PostgreSQL 拒绝：23/23（详见 json）

## 结论

PRAGMA 与 SQLite DDL **不迁移**：它们属于「不重放历史 Alembic、改用审查后
PG baseline」的范畴。本表的作用是**穷举**它们，确保没有遗漏项被静默保留。

