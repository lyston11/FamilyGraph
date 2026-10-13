# PostgreSQL schema 可行性实测（2026-10-04）

在隔离 `postgres:16-alpine`（开发服务器，独立库）上逐表建表。未接触开发库或线上库。

## 结论：87 张表中 84 张可直接建立，1 类构造是唯一阻塞项

逐表在真实 PostgreSQL 上执行 `Table.create()`（不是「编译 DDL 看看」，而是真的建）：

```
表总数 87，成功 84，失败 3
  memory_candidates                 UndefinedFunction: function json_extract(json, unknown) does not exist
  memories                          UndefinedFunction: function json_extract(json, unknown) does not exist
  rag_index_maintenance_failures    UndefinedTable: relation "memories" does not exist   ← 级联
```

修掉 `json_extract` 后：**87/87 全部建立成功**，含 252 个索引（其中 16 个局部）、
116 个 CHECK、212 个 FK。

**这意味着不需要「重写全部历史 Alembic」**：ORM 元数据本身在 PostgreSQL 上是可建的，
真正需要人工审查的是**语义**（约束是否表达同一判据），而不是语法能否通过。

## 唯一阻塞项：SQLite JSON 函数

`memory.py` 的 source-snapshot CHECK 用了 `json_extract`，PostgreSQL 无此函数，
**建表直接失败**（不是「约束不生效」，而是表建不出来）。

SQLAlchemy 的 `CheckConstraint` 只接受一个表达式，**没有** `sqlite_where` /
`postgresql_where` 那样的方言分派机制。因此新增 `app/models/checks.py::DialectCheck`，
用 `@compiles` 按方言渲染。

## 语义等价性：逐值验证，不靠阅读

SQLite 的 `json_extract(col,'$.version') = 1` **不是**简单的数值比较：

| JSON 载荷 | `= 1` | 说明 |
|---|---|---|
| `{"version":1}` | true | 数字 1 |
| `{"version":"1"}` | **false** | 字符串 `"1"` ≠ 数字 1 |
| `{"version":2}` | false | |
| `{"version":true}` | **true** | SQLite 把 JSON `true` 折成整数 1 |
| `{"version":null}` / `{}` | NULL → coalesce 后 false | |

因此 PostgreSQL 侧**不能**写 `(col::jsonb ->> 'version')::int = 1`——那会接受 `"1"`，
把约束放松到 SQLite 语义**之外**。等价式是 jsonb 对 jsonb 比较：

```sql
(source_span_json::jsonb -> 'version') = '1'::jsonb
```

`tests/test_dialect_checks.py` 用 **SQLite 自身的求值结果**作为真源，对 9 个载荷 ×
2 个 verification 状态逐个断言两方言一致（22 个用例）。

## 已知且刻意保留的分歧：JSON 布尔

SQLite 把 `true` 折成 `1`，PostgreSQL 保留类型。`{"version":true}` 在 SQLite 通过、
在 PG 被拒。判定为**可接受**，依据是可达性：

- `version` 由 `ExactChunkRef.as_json()` 写死为 Python 整数字面量 `{"version": 1, ...}`；
- `source_kind` 只能取 `MEMORY_SOURCE_KINDS` 中的字符串，永不为数字或布尔；
- 真实库中 `memory_candidates` / `memories` **各 0 行**（开发库实测）。

因此分歧只存在于应用不会写出的形状上，且 PG 侧方向是**更严格**（拒绝而非放行）。
用例把两个方向都钉住：既记录该分歧存在，也断言不得为消除它而把 PG 表达式放松成接受字符串。

## SQLite 侧逐字不变

改动后 SQLite 渲染出的 CHECK 与 `main` 上**逐字相同**（约束名与表达式均一致），
因此**不需要新迁移**，既有库无需变更。已对比确认。

## 仍待验证

- **约束语义**的其余部分：116 个 CHECK 中只有这一个用了方言专属函数；其余是标准
  SQL 比较，但「标准 SQL 在两方言上是否真的同义」（如字符串排序、NULL 处理、
  整数溢出）尚未逐条验证。
- **数据导入**：本次只验证 schema 可建，未验证历史数据能导入（需要真实快照与对账）。
- **Alembic 与 PG**：本次用 ORM `create_all` 验证可行性；实际 baseline 迁移仍需
  显式书写（不能用 `create_all` 冒充迁移，见 `database-guidelines.md`）。
- `rag_index_maintenance_failures` 的失败是 `memories` 未建成的级联，不是独立问题。
