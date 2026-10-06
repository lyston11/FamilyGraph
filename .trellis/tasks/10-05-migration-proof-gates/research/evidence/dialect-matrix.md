
## 差异项逐项结论（Gate 1 要求：保留 / 适配 / 拒绝）

### 1. 整数与布尔混用 —— **保留（可达性为零）**

```
SQLite    : SELECT 1 = TRUE  ->  1
PostgreSQL: SELECT 1 = TRUE  ->  UndefinedFunction
```

**依据（可达性，不是「差不多」）**：

- 模型层用 `Boolean` 或 `Integer` 显式声明列类型，不混用；
- 扫描确认 `app/` 中不存在「整数列与布尔字面量比较」的 SQL（`build_raw_sql_inventory`
  的 `sqlite_only_function` 类别为 6 项，均为 JSON 相关，无布尔混用）；
- 该差异只存在于应用不会写出的形状上。

**若将来出现**：PG 会**报错**（而非静默给出不同结果），因此属于 fail-loud，不会造成
静默数据错误。这正是可以「保留」的理由。

### 2. JSON 布尔取值 —— **保留（已由 DialectCheck 处理，且更严格）**

```
SQLite    : json_extract('{"b":true}','$.b')  ->  1（整数）
PostgreSQL: '{"b":true}'::jsonb ->> 'b'       ->  'true'（文本）
```

**依据**：这与 `tests/test_dialect_checks.py` 已记录的已知分歧一致，且当时已判定可接受：

- `source_span_json` 的 `version` 由 Python 整数字面量产生（`{"version": 1, ...}`）；
- `source_kind` 只取字符串枚举；
- 真实库中布尔型 `version` 为 **0 行**；
- 方向是 PG **更严格**（拒绝而非放行）。

`DialectCheck` 用 jsonb 对 jsonb 比较保留了类型敏感语义，且测试断言
**不得**为消除该分歧而把表达式放松成接受字符串 `"1"`。

## 结论

13 项中 **11 项语义一致**，2 项差异均判定为**保留**，且都满足「PG 侧 fail-loud 或更严格」
这一条件——不存在「静默给出不同结果」的差异。

**但本矩阵不等于 Gate 1 通过**：它覆盖的是通用语义维度，尚未覆盖
`build_raw_sql_inventory` 列出的全部 31 处 PRAGMA 与 23 处 SQLite DDL 的逐条执行。
