"""索引构造辅助：跨方言声明同一个局部谓词。

## 为什么需要这个模块

SQLAlchemy 只在**匹配的方言**下渲染方言专属谓词。因此

```python
Index("uq_x", "session_id", unique=True, sqlite_where=text("status = 'active'"))
```

在 SQLite 上是局部唯一索引，在 PostgreSQL 上却会渲染成

```sql
CREATE UNIQUE INDEX uq_x ON t (session_id)   -- 谓词消失
```

即**退化为全表唯一索引**。这不是「少了个索引」而是唯一性范围被放大到整张表：
`UNIQUE(session_id)` 意味着一个 session 一生只能有一行，历史终态行会直接撞约束。

仓库曾出现 16 处这样的索引、0 处 `postgresql_where`。修复方式不是逐处补参数
（会再次漂移），而是让两处方言谓词来自**同一个字符串**，由本模块统一生成。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa


def partial_unique_index(
    name: str,
    *columns: Any,
    where: str,
) -> sa.Index:
    """局部唯一索引，同时在 SQLite 与 PostgreSQL 上生效。

    `where` 是**一个** SQL 片段，被同时用于两个方言，因此不存在「改了一边忘了另一边」
    的可能。`columns` 可以是列名或表达式（如 `sa.text("COALESCE(space_id, -1)")`）。

    调用方不需要（也不应该）再手写 `sqlite_where` / `postgresql_where`：
    `tests/test_partial_index_portability.py` 会断言 `app/models` 中不存在只声明单一
    方言谓词的唯一索引。
    """
    if not where or not where.strip():
        raise ValueError(f"partial index {name!r} requires a non-empty predicate")
    # 两个独立的 TextClause 实例：同一对象被两个方言共享时，SQLAlchemy 的内部
    # 缓存与编译状态可能互相干扰，分开更安全。
    return sa.Index(
        name,
        *columns,
        unique=True,
        sqlite_where=sa.text(where),
        postgresql_where=sa.text(where),
    )
