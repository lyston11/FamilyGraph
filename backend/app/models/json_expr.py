"""对 **Text 列**存 JSON 的场景，提供方言感知的取值表达式。

## 为什么不能用 SQLAlchemy 的 `cast(col, JSON)["k"]`

对 JSON 类型的列，`col["k"].as_integer()` 是可移植的（SQLite 渲染 `JSON_EXTRACT`，
PostgreSQL 渲染 `->`/`->>` + `CAST`），`app/services/steward_delivery.py` 已在用。

但 `audit_log.detail_json` 是 **Text** 列（模型里显式用 `json.loads` 反序列化）。
对它套 `cast(col, JSON)` 在 SQLite 上是**错的**：

```sql
-- SQLite：CAST(x AS JSON) 不求值为 JSON 文本，而是整数 0
SELECT typeof(CAST('{"space_id":5}' AS JSON));   -- 'integer'
SELECT json_extract(CAST('{"space_id":5}' AS JSON), '$.space_id');  -- NULL
```

实测确认：期望 5，实际得到 NULL。因此这里不用 cast，而是按方言直接渲染：

- SQLite：`json_extract(col, '$.k')`
- PostgreSQL：`(col::jsonb ->> 'k')::integer`

PostgreSQL 侧对**非 JSON 文本**会抛 `InvalidTextRepresentation`，而 SQLite 静默返回
NULL。这一差异是有意保留的：`detail_json` 的写入方始终写 `json.dumps(...)`，非 JSON
内容属于数据损坏，在迁移期**报错比静默跳过更安全**（静默会让去重查询漏配，产生重复建议）。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import ColumnElement


# 模型的 `InstrumentedAttribute[str]` 在运行时是合法的列表达式，但不是
# `ColumnElement` 的静态子类。用 Protocol 表达这个交集，避免在调用点 cast。
class _SqlExpression(Protocol):
    def __clause_element__(self) -> Any: ...


class JsonTextExtract(ColumnElement[Any]):
    """从 Text 列里按方言提取 JSON 字段。"""

    inherit_cache = False

    def __init__(self, column: _SqlExpression, path: str, as_type: str) -> None:
        if not path or path.startswith("$.") is False and path.startswith("$[") is False:
            raise ValueError(f"JSON path must start with '$.' or '$[', got {path!r}")
        if as_type not in ("string", "integer"):
            raise ValueError(f"unsupported as_type: {as_type!r}")
        self.column = column
        self.path = path
        self.as_type = as_type
        # SQLite 侧直接用 json_extract；类型转换交给比较运算（SQLite 动态类型）。
        self.type = sa.String() if as_type == "string" else sa.Integer()


@compiles(JsonTextExtract, "sqlite")
def _sqlite(element: JsonTextExtract, compiler, **kw) -> str:  # type: ignore[no-untyped-def]
    col = compiler.process(element.column, **kw)
    return f"json_extract({col}, '{element.path}')"


@compiles(JsonTextExtract, "postgresql")
def _postgresql(element: JsonTextExtract, compiler, **kw) -> str:  # type: ignore[no-untyped-def]
    col = compiler.process(element.column, **kw)
    # `->>` 取出文本，再按目标类型显式转换，使比较与 SQLite 的数值语义对齐。
    pg_type = "VARCHAR" if element.as_type == "string" else "INTEGER"
    return f"CAST(({col})::jsonb ->> '{element.path[2:]}' AS {pg_type})"


def json_text_field(
    column: _SqlExpression,
    path: str,
    *,
    as_type: Literal["string", "integer"] = "string",
) -> JsonTextExtract:
    """`path` 形如 `$.space_id`；返回可在两方言上求值的表达式。"""
    return JsonTextExtract(column, path, as_type)
