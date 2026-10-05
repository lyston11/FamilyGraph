"""方言感知的 CHECK 约束辅助。

## 为什么需要

部分 CHECK 约束用 SQLite 的 JSON 函数表达（`json_extract(col, '$.k')`）。PostgreSQL
没有 `json_extract`，因此这类约束会让**建表本身失败**（实测 87 张表中有 2 张因此建不出来）。

SQLAlchemy 的 `CheckConstraint` 只接受一个 SQL 表达式，**没有** `sqlite_where` /
`postgresql_where` 那样的方言分派。因此这里用 `@compiles` 为方言渲染不同文本。

## 语义必须逐字等价

SQLite 的 `json_extract(col,'$.version') = 1` 是**类型敏感**的：JSON 数字 `1` 相等，
字符串 `"1"` **不**相等。直接写成 PG 的 `(col::jsonb ->> 'version') = 1` 会因
`text = integer` 报错，写成 `::int` 则会把 `"1"` 也判为相等——两者都不等价。

等价式是 `(col::jsonb -> 'version') = '1'::jsonb`（jsonb 对 jsonb 比较，保留类型）。
`app/tests/test_dialect_checks.py` 对 7 个边界值逐个断言两方言结果一致。
"""

from __future__ import annotations

from sqlalchemy import schema as sa_schema
from sqlalchemy.ext.compiler import compiles


class DialectCheck(sa_schema.CheckConstraint):
    """同一个约束，按方言渲染不同表达式。

    `sqlite_expr` 与 `postgres_expr` 必须表达**相同**的判据；本类只负责渲染，
    不做等价性检查——等价性由测试保证。
    """

    def __init__(self, *, sqlite_expr: str, postgres_expr: str, name: str) -> None:
        if not sqlite_expr.strip() or not postgres_expr.strip():
            raise ValueError(f"constraint {name!r} requires both dialect expressions")
        self.sqlite_expr = sqlite_expr
        self.postgres_expr = postgres_expr
        # 传给父类的表达式仅作为默认（非 PG）渲染路径；SQLite 由下面的 compiler 覆盖。
        super().__init__(sqlite_expr, name=name)


@compiles(DialectCheck, "postgresql")
def _compile_pg(element: DialectCheck, compiler, **kw) -> str:  # type: ignore[no-untyped-def]
    return f"CONSTRAINT {element.name} CHECK ({element.postgres_expr})"
