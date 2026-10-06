# Gate 1：历史 Alembic 无法在 PostgreSQL 上重放的实测证据

## 结论

三条历史迁移包含 SQLite 专属构造，在真实 PostgreSQL 上**确认失败**（不是阅读代码推断）。
因此 `postgres-migration` 的设计结论——**不在 PostgreSQL 上重放历史 Alembic，改用审查后的
PG baseline**——是必需的，而不是风格偏好。

## 实测（隔离 `postgres:16-alpine`，独立 database，L2 证据）

```
[ERR] 0042 json_extract CHECK: UndefinedFunction: function json_extract(json, unknown) does not exist
[ERR] 0022 last_insert_rowid: UndefinedFunction: function last_insert_rowid() does not exist
[ERR] 0014 FTS5 virtual table: SyntaxError: syntax error at or near "VIRTUAL"
[OK ] 修复后的方言感知 CHECK 在同一 PostgreSQL 上建立成功
```

| 迁移 | 构造 | PostgreSQL 错误 |
|---|---|---|
| `0042_memory_source_contract.py:28-29` | `json_extract(...)` 写入 `CheckConstraint` | `UndefinedFunction` |
| `0022_system_admin_space_manager.py:104` | `SELECT last_insert_rowid()` | `UndefinedFunction` |
| `0014_memory_rag.py:245` | `CREATE VIRTUAL TABLE ... USING fts5(...)` | `SyntaxError` |

最后一行是**反证**：同一实例上，修复后的方言感知形态
（`coalesce((col::jsonb -> 'version') = '1'::jsonb, false)`）建立成功。说明失败来自 SQLite
专属构造本身，不是 PostgreSQL 配置或权限问题。

## 与 `pg-schema-feasibility.md` 的关系（重要，避免误读）

两者**不矛盾**，但绝不能互相替代：

| 路径 | 结论 | 证据 |
|---|---|---|
| ORM metadata 建表 | 87/87 表可建 | `pg-schema-feasibility.md`（L2） |
| 历史 Alembic 重放 | **3 条迁移失败** | 本文件（L2） |

`create_all` 走的是 ORM 元数据，绕过了历史迁移链，因此看不到这三处。
把「metadata 可建」当作「迁移链可重放」会**静默漏掉三个真实阻塞**。

## 对后续 Gate 的要求

- Gate 3 的 PostgreSQL prototype 必须使用**审查后的 baseline**，并在文档中说明它替代了哪段历史迁移；
- 任何「schema 可建」的声明必须注明是 metadata 路径还是 migration 路径；
- `fts5` 的检索语义由 `10-04-lexical-search-migration` 负责，本任务只确认它**阻止重放**。

## 证据等级

**L2**：真实隔离 PostgreSQL 单连接执行。尚未做多连接、迁移往返或 baseline 对账（L3）。

## 可复跑产物（把 L2 从断言变成证据）

```bash
PGTEST_DSN=postgresql://postgres:probe@<隔离主机>:5432/familygraph \
  ./backend/.venv/bin/python research/tools/pg_replay_probe.py
```

退出码：`0` = 全部符合预期；`1` = 有断言不符；`2` = 缺少 DSN 或驱动（环境阻塞，不算通过）。

覆盖 7 个用例：三条历史迁移阻塞（0042/0022/0014）、**两个 SQLite 触发器**（0009/0045）、
修复后的方言感知 CHECK（反证）、PostgreSQL 原生触发器形态（对照）。

`PGTEST_DSN` 必须指向隔离实例；脚本不接受默认 DSN，避免误连开发库或线上。
