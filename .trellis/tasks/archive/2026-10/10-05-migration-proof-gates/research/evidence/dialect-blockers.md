# Gate 1：SQLite 专属构造的真实命中（AST 分类）

初版扫描把注释和文档字符串里的 `json_extract` 也计入，得到 16 处虚假命中。本文件用 AST
区分 `code` / `string-literal` / `docstring` / `comment` / `test`，只保留**会影响执行**的命中。

## 分类统计

| 类别 | 数量 |
|---|---|
| code | 5 |
| comment | 4 |
| docstring | 5 |
| string-literal | 14 |
| test | 47 |

## 真实代码命中（排除测试）

| 路径:行 | 类别 | 影响 |
|---|---|---|
| `backend/app/backup.py:20` | string-literal | Python 端 `datetime.strftime`，不是 SQL 函数——**无影响** |
| `backend/app/dev_seed.py:833` | string-literal | Python 端 `datetime.strftime`，不是 SQL 函数——**无影响** |
| `backend/app/models/json_expr.py:62` | string-literal | PostgreSQL 无此函数：CHECK 表达式会**阻止建表**；运行期查询会在**执行时**失败 |
| `backend/app/models/memory.py:41` | string-literal | PostgreSQL 无此函数：CHECK 表达式会**阻止建表**；运行期查询会在**执行时**失败 |
| `backend/app/models/memory.py:42` | string-literal | PostgreSQL 无此函数：CHECK 表达式会**阻止建表**；运行期查询会在**执行时**失败 |
| `backend/app/models/rag.py:135` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/app/models/rag.py:156` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/app/services/memory_rag.py:52` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/app/services/memory_rag.py:683` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/app/services/memory_rag.py:684` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/migrations/versions/0014_memory_rag.py:226` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/migrations/versions/0014_memory_rag.py:245` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/migrations/versions/0022_system_admin_space_manager.py:104` | string-literal | SQLite 专属函数，在 PostgreSQL 上不存在 |
| `backend/migrations/versions/0042_memory_source_contract.py:28` | code | PostgreSQL 无此函数：CHECK 表达式会**阻止建表**；运行期查询会在**执行时**失败 |
| `backend/migrations/versions/0042_memory_source_contract.py:29` | code | PostgreSQL 无此函数：CHECK 表达式会**阻止建表**；运行期查询会在**执行时**失败 |
| `backend/migrations/versions/0047_rag_lifecycle_integrity.py:103` | string-literal | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/migrations/versions/0047_rag_lifecycle_integrity.py:123` | code | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/migrations/versions/0048_steward_terminology_publication.py:200` | code | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |
| `backend/migrations/versions/0055_steward_assist_execution_unit.py:410` | code | FTS5 虚拟表 / index_version 字符串；PG 无 FTS5，需要独立检索方案 |

## 需要区分的两类命中

### 1. 模型层（决定 PostgreSQL schema 能否建立）

- `app/models/memory.py:41-42`：CHECK 表达式。**已修复**——现在经
  `app/models/checks.py::DialectCheck` 按方言渲染（SQLite `json_extract` / PG `jsonb`）。
- `app/models/json_expr.py:62`：`json_text_field` 的 **SQLite 分支**，是刻意保留的方言渲染，不是缺陷。
- `app/models/rag.py`、`memory_rag.py` 的 `fts5-trigram-v*`：是 **index_version 标识字符串**，
  不是 SQL。它们记录的是历史算法名，不能因为名字含 "fts5" 就当作 SQL 缺陷。

### 2. 历史迁移（决定能否在 PostgreSQL 上重放 Alembic）

- `migrations/versions/0042_memory_source_contract.py:28-29`：**真实阻塞**。该迁移把裸
  `json_extract` 写进 `CheckConstraint`，在 PostgreSQL 上会阻止 `upgrade head`。
- `migrations/versions/0022_system_admin_space_manager.py:104`：`SELECT last_insert_rowid()`，
  PostgreSQL 无此函数，会阻止该迁移。
- `migrations/versions/0014_memory_rag.py:245`：`CREATE VIRTUAL TABLE ... USING fts5(...)`，
  PostgreSQL 无 FTS5，会阻止该迁移。

这三条与 `solution-decision.md` 的结论一致：**不在 PostgreSQL 上重放历史 Alembic**，
而是使用审查后的 PostgreSQL baseline。但必须显式确认这三处，而不是假定「schema 能建出来」。

## 与先前结论的关系

- `pg-schema-feasibility.md` 的「87/87 表可建」是用 **ORM metadata** 建表，绕过了历史迁移，
  因此不受这三处影响。两者不矛盾：**metadata 可建 ≠ 历史迁移可重放**。
- 这是 Gate 1 需要明确记录的边界，避免把前者误当作后者。

## 证据等级

**L0**（AST 源码分类）。`fts5` 的检索语义、`last_insert_rowid` 的替代实现和
baseline 迁移的正确性都需要 L2/L3 验证，尚未完成。
