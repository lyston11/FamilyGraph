"""运行期查询不得依赖 SQLite 专属函数（结构性回归）。

## 背景（真实缺陷）

`app/` 中有 11 处用 `func.json_extract(col, "$.k")` 做运行期查询。这些**不是**建表
问题（表能建出来），而是在 PostgreSQL 上**执行时**才报
`UndefinedFunction: function json_extract(...) does not exist` —— 也就是说它们会
通过全部 SQLite 测试、通过全部建表检查，然后在 PostgreSQL 上第一次被调用时失败。

已修复的形态是把它们换成 SQLAlchemy 的 JSON 索引表达式，它按方言渲染：

| 写法 | SQLite | PostgreSQL | 可移植 |
|---|---|---|---|
| `col["k"].as_string()`（JSON 列） | `JSON_EXTRACT(col, '$.k')` | `col ->> 'k'` | ✅ |
| `col["k"].as_integer()`（JSON 列） | `JSON_EXTRACT(...)` | `CAST(col ->> 'k' AS INTEGER)` | ✅ |
| `json_text_field(col, "$.k")`（**Text** 列） | `json_extract(col,'$.k')` |
  `CAST(col::jsonb ->> 'k' AS ...)` | ✅ |
| `func.json_extract(col, "$.k")` | `json_extract(...)` | `json_extract(...)` ← 不存在 | ❌ |

## 为什么 Text 列不能套 `cast(col, JSON)`

SQLite 的 `CAST(x AS JSON)` 不求值为 JSON 文本，而是整数 `0`：

```sql
SELECT typeof(CAST('{"a":1}' AS JSON));        -- 'integer'
SELECT json_extract(CAST('{"a":1}' AS JSON), '$.a');  -- NULL（期望 1）
```

实测确认。因此 Text 列必须走 `json_text_field`（按方言直接渲染），不能靠 cast。
"""

from __future__ import annotations

import ast
import pathlib
import re

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# SQLite 专属函数/构造：出现在 SQL 表达式中即不可移植。
SQLITE_ONLY_SQL_TOKENS = (
    "json_extract",
    "strftime",
    "julianday",
    "AUTOINCREMENT",
    "WITHOUT ROWID",
    "last_insert_rowid",
    "group_concat",
)

# 允许出现的例外：(相对 app/ 的路径, 行号) -> 理由。
# 目前为空——所有 json_extract 都应通过方言感知的辅助表达。
ALLOWED: dict[tuple[str, int], str] = {}


def _sql_expression_calls() -> list[tuple[str, int, str]]:
    """找出 `func.<name>(...)` 形式的调用（SQL 表达式，不是字符串）。"""
    found = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        src = path.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rel = str(path.relative_to(APP_DIR.parent))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in SQLITE_ONLY_SQL_TOKENS:
                continue
            # 只关心 sa.func.x / func.x 形态
            base = ast.unparse(func.value)
            if base not in ("func", "sa.func", "sqlalchemy.func"):
                continue
            found.append((rel, node.lineno, func.attr))
    return found


def test_no_runtime_query_uses_sqlite_only_functions():
    """运行期 SQL 表达式不得调用 SQLite 专属函数。"""
    offenders = []
    for rel, line, name in _sql_expression_calls():
        if (rel, line) in ALLOWED:
            continue
        offenders.append(f"{rel}:{line}  func.{name}(...)")
    assert not offenders, (
        "发现 SQLite 专属函数被用于运行期 SQL 表达式——它们在 SQLite 上通过全部测试，"
        "却会在 PostgreSQL 上第一次执行时报 UndefinedFunction。改用 SQLAlchemy 的 JSON "
        "索引形式（JSON 列）或 app.models.json_expr.json_text_field（Text 列）：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_json_extract_call_sites_are_actually_gone():
    """盘点基线：`func.json_extract` 的出现次数必须为 0。"""
    n = sum(1 for _r, _l, name in _sql_expression_calls() if name == "json_extract")
    assert n == 0, f"仍有 {n} 处 func.json_extract 调用"


def test_json_index_expressions_render_per_dialect():
    """JSON 列上的索引表达式必须在两方言下都渲染成各自的原生形式。"""
    from sqlalchemy.dialects import postgresql as pg
    from sqlalchemy.dialects import sqlite as sq

    from app.models.steward import StewardGeneration

    col = StewardGeneration.input_versions_json
    for expr, expect_sqlite, expect_pg in (
        (col["version"].as_string(), "JSON_EXTRACT", "->>"),
        (col["global"][0].as_integer(), "JSON_EXTRACT", "->>"),
    ):
        s = str(expr.compile(dialect=sq.dialect()))
        p = str(expr.compile(dialect=pg.dialect()))
        assert expect_sqlite in s, f"SQLite 渲染异常：{s}"
        assert expect_pg in p, f"PostgreSQL 渲染异常：{p}"
        assert "json_extract" not in p.lower(), f"PostgreSQL 上仍渲染出 json_extract：{p}"


def test_text_column_helper_renders_per_dialect():
    """Text 列的辅助必须在两方言下渲染成各自的原生形式。"""
    from sqlalchemy.dialects import postgresql as pg
    from sqlalchemy.dialects import sqlite as sq

    from app.models.audit_log import AuditLog
    from app.models.json_expr import json_text_field

    expr = json_text_field(AuditLog.detail_json, "$.space_id", as_type="integer")
    s = str(expr.compile(dialect=sq.dialect()))
    p = str(expr.compile(dialect=pg.dialect()))
    assert "json_extract" in s, f"SQLite 应使用 json_extract：{s}"
    assert "json_extract" not in p.lower(), f"PostgreSQL 不应出现 json_extract：{p}"
    assert "jsonb" in p and "->>" in p, f"PostgreSQL 应使用 jsonb ->>：{p}"
    assert "INTEGER" in p, f"integer 目标类型应显式转换：{p}"


def test_text_column_helper_rejects_bad_input():
    """辅助本身拒绝非法 path 与类型，避免误用。"""
    import pytest

    from app.models.audit_log import AuditLog
    from app.models.json_expr import json_text_field

    with pytest.raises(ValueError):
        json_text_field(AuditLog.detail_json, "space_id")  # 缺 $. 前缀
    with pytest.raises(ValueError):
        json_text_field(AuditLog.detail_json, "$.x", as_type="float")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# boolean 列不得与整数比较（2026-10-10 真实事故）
# ---------------------------------------------------------------------------
#
# SQLite 无严格类型，boolean 列存 0/1，所以 `cancel_requested = 0` 在 SQLite 上完全
# 正常；PostgreSQL 的列是真正的 `boolean`，同一语句直接报
# `UndefinedFunction: operator does not exist: boolean = integer`。
#
# 实测后果：2026-10-08 切到 PostgreSQL 当天，`agent_tools` 与 `provider_proxy` 两处
# 准入 CAS 同时失效，**每一次 agent 工具调用都 500**（生产 2324 次/72h），而 SQLite
# 测试全绿。因此与 `json_extract` 同性质，放在同一处守卫。

_MODEL_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "models"
_SQL_KEYWORDS = re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE|WHERE)\b", re.IGNORECASE)


def _boolean_column_names() -> set[str]:
    """从模型声明动态收集 boolean 列名（`Mapped[bool]`），不手写清单。

    手写清单会随模型演化而失效——那正是守卫静默失效的形态。
    """
    names: set[str] = set()
    for path in sorted(_MODEL_DIR.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            if ast.unparse(node.annotation) == "Mapped[bool]":
                names.add(node.target.id)
    return names


def _string_literals() -> list[tuple[str, int, str]]:
    """收集 app/ 下所有字符串字面量（raw SQL 以相邻字面量隐式拼接）。"""
    out: list[tuple[str, int, str]] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        rel = str(path.relative_to(APP_DIR.parent))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append((rel, node.lineno, node.value))
    return out


def test_no_raw_sql_compares_a_boolean_column_to_an_integer():
    """raw SQL 不得把 boolean 列与 0/1 比较——PostgreSQL 上直接报类型错误。"""
    boolean_columns = _boolean_column_names()
    assert boolean_columns, "未能从模型收集到 boolean 列——守卫已失效，先修收集逻辑"
    pattern = re.compile(r"\b(" + "|".join(sorted(boolean_columns)) + r")\s*=\s*[01]\b")

    offenders = []
    for rel, line, value in _string_literals():
        # 只检查看起来像 SQL 的字面量：文档字符串里也会出现 `active = 0` 这类描述。
        if not _SQL_KEYWORDS.search(value):
            continue
        for match in pattern.finditer(value):
            offenders.append(f"{rel}:{line}  {match.group(0)}")
    assert not offenders, (
        "发现 raw SQL 把 boolean 列与整数比较。SQLite 上正常，PostgreSQL 上会报 "
        "`operator does not exist: boolean = integer`，且在 SQLite 测试里看不出来。"
        "改用 `= FALSE` / `= TRUE` 字面量（两方言都支持）或绑定布尔参数：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
