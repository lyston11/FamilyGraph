# Gate 1：结构化 raw SQL inventory（已排除误报）

## 计数

| 风险类别 | 数量 | 迁移影响 |
|---|---|---|
| `sqlite_only_function` | 6 | PostgreSQL 无此函数：建表或执行时失败 |
| `sqlite_pragma` | 31 | 无 PRAGMA，需改为 SET / 连接参数 |
| `sqlite_ddl` | 23 | 虚拟表/FTS5 不存在；触发器需 plpgsql |
| `transaction_control` | 3 | `BEGIN IMMEDIATE` 语义需 CAS/行锁/counter |
| `literal_autoincrement_keyword` | 0 | 字面量 AUTOINCREMENT（本仓库为 0） |
| 方言中立 SQL | 1475 | 仅需索引与执行计划复核 |

## 已排除的两类误报（实测，不是阅读）

### 1. `autoincrement=True` 可移植

初版把 6 处 `autoincrement=True` 计入阻塞。实测 SQLAlchemy 的双方言渲染：

```
SQLite : CREATE TABLE probe ( id INTEGER NOT NULL, n VARCHAR(10), PRIMARY KEY (id) )
PG     : CREATE TABLE probe ( id SERIAL NOT NULL, n VARCHAR(10), PRIMARY KEY (id) )
```

`autoincrement=True` 是 **SQLAlchemy 参数**而非 SQL 关键字，PostgreSQL 渲染为 `SERIAL`。
真正不可移植的是**字面量 SQL 关键字** `AUTOINCREMENT`，本仓库为 **0 处**。

### 2. `strftime` 是 Python 方法

`app/backup.py:20` 与 `dev_seed.py:833` 的 `datetime.now(UTC).strftime(...)` 是 **Python**
字符串格式化，不进入 SQL（实测可正常执行）。因此已从 `sqlite_only_function` 排除。

### 3. 行尾注释

`steward.py:421` 的 `# autoincrement after flush` 是行尾注释。扫描器原先只排除整行注释，
现改用 `tokenize` 精确识别 `COMMENT` token。

## 真实命中（6 处 `sqlite_only_function`）

| 位置 | 性质 |
|---|---|
| `backend/app/models/json_expr.py:62` | 方言渲染（刻意保留） |
| `backend/app/models/memory.py:41` | 模型 CHECK（已修） |
| `backend/app/models/memory.py:42` | 模型 CHECK（已修） |
| `backend/migrations/versions/0022_system_admin_space_manager.py:104` | 历史迁移（阻塞重放） |
| `backend/migrations/versions/0042_memory_source_contract.py:28` | 历史迁移（阻塞重放） |
| `backend/migrations/versions/0042_memory_source_contract.py:29` | 历史迁移（阻塞重放） |

## 已实测的部分

- `0042` 的 `json_extract` CHECK：`pg_replay_probe.py` 实测 `UndefinedFunction`；
- 3 个运行期查询：已在真实 PostgreSQL 编译并执行成功；
- FTS5 与 2 个触发器：`pg_replay_probe.py` 实测语法错误；
- 反向锁序：`pg_deadlock_probe.py` 实测死锁。

## 未实测

- `sqlite_pragma` 的 31 处尚未逐条在 PostgreSQL 上验证（多数在迁移里做表重建，
  属于「不重放历史迁移」的范畴）；
- `sqlite_ddl` 的 23 处中只有 FTS5 与 2 个触发器实测，其余 20 处待验证；
- 方言中立 SQL 的 1475 处未做执行计划与索引复核。

## 证据等级

结构化条目为 **L0**；标注 `L2-partial` 的类别有部分实测，但**未覆盖全部条目**。
