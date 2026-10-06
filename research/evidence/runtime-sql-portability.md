# 运行期 SQL 的方言可移植性（2026-10-04）

## 结论：11 处运行期查询依赖 SQLite 专属函数

`func.json_extract(col, "$.k")` 在 `app/` 中出现 11 次，分布在 3 个文件：

| 文件 | 处数 | 列类型 |
|---|---|---|
| `services/steward_gc.py` | 6 | JSON |
| `api/admin_steward.py` | 1 | JSON |
| `services/steward_suggestions.py` | 2 | **Text** |

这些**不是建表问题**（表能建出来），而是在 PostgreSQL 上**第一次执行**时才报
`UndefinedFunction: function json_extract(...) does not exist`。也就是说：它们能通过
全部 SQLite 测试、通过全部建表检查，然后在生产第一次被调用时失败——比建表失败更隐蔽。

## 修复：用 SQLAlchemy 的方言感知表达式

### JSON 列：`col["k"].as_string() / .as_integer()`

实测编译结果：

| 写法 | SQLite | PostgreSQL |
|---|---|---|
| `col["version"].as_string()` | `JSON_EXTRACT(col, ?)` | `col ->> %s` |
| `col["global"][0].as_integer()` | `JSON_EXTRACT(JSON_QUOTE(JSON_EXTRACT(col, ?)), ?)` | `CAST((col -> %s) ->> %s AS INTEGER)` |

**这不是新引入的写法**：`services/steward_delivery.py` 的 `valid_generation` 查询
早已在用同一形式，因此本次是**消除不一致**，而不是引入新模式。

逐值验证（真实数据 `{"version":"snap-v1","global":[7,9],...}`）：7 条路径的
SQLite 旧表达式与 PostgreSQL 新表达式取值完全一致。

### Text 列：`models/json_expr.json_text_field`

`audit_log.detail_json` 是 **Text** 列（模型里显式 `json.loads` 反序列化），
**不能**套 `cast(col, JSON)`：

```sql
-- SQLite：CAST(x AS JSON) 不求值为 JSON 文本，而是整数 0
SELECT typeof(CAST('{"space_id":5}' AS JSON));          -- 'integer'
SELECT json_extract(CAST('{"space_id":5}' AS JSON), '$.space_id');  -- NULL（期望 5）
```

实测确认（期望 5、实际 NULL）。因此 `json_text_field` 按方言直接渲染：

- SQLite：`json_extract(col, '$.k')`
- PostgreSQL：`CAST((col)::jsonb ->> 'k' AS INTEGER)`

### 一处刻意保留的行为差异

PostgreSQL 对**非 JSON 文本**会抛 `InvalidTextRepresentation`，SQLite 静默返回 NULL。
判定为可接受且**更安全**：`detail_json` 的写入方始终写 `json.dumps(...)`，非 JSON 内容
属于数据损坏；静默返回 NULL 会让去重查询漏配并产生重复建议，报错则立即暴露。

## 验证

- 三个改动后的查询在**真实 PostgreSQL** 上编译（无 `json_extract`）并**执行成功**。
- 结构性回归 `tests/test_sql_portability.py`：`app/` 中不得出现
  `func.<SQLite 专属函数>`；JSON 索引表达式与 Text 辅助必须在两方言下渲染成各自原生形式。
- 变异验证：把 `json_text_field` 换回 `func.json_extract` → **2 个用例失败**。
- 既有功能回归：`test_steward_suggestion_quality.py` 47 项通过。

## 仍待验证

- **其余 SQLite 专属构造**：`strftime`/`julianday` 等只出现在 Python 侧
  （`datetime.strftime`），不在 SQL 中；但字符串排序、NULL 排序、整数除法等
  **非函数**语义差异尚未逐条验证。
- **数值类型的比较语义**：SQLite 动态类型下 `json_extract` 返回 int，PostgreSQL 的
  `->>` 返回 text 需显式 `CAST`；本次靠 `.as_integer()` 处理，但**未覆盖所有比较路径**
  （例如与子查询结果比较时的类型推导）。
