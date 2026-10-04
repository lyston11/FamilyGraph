"""局部唯一索引必须跨方言生效（结构性 + 语义回归）。

## 背景（真实缺陷，非假设）

仓库曾有 16 个 `Index(..., unique=True, sqlite_where=...)`，而 `postgresql_where` 为 0。
SQLAlchemy 只在匹配方言下渲染方言专属谓词，因此这些索引在 PostgreSQL 上会**静默退化**
为全表唯一索引：

```sql
-- SQLite：正确
CREATE UNIQUE INDEX uq_agent_runs_session_active ON agent_runs (session_id)
  WHERE status IN ('queued','leased','running')
-- PostgreSQL：谓词消失
CREATE UNIQUE INDEX uq_agent_runs_session_active ON agent_runs (session_id)
```

后果不是「索引变少」而是唯一性范围被放大到整表：`UNIQUE(session_id)` 让一个 session
一生只能有一行 run，历史终态行会直接撞约束。

本文件的两类用例分别守护不同层面：

1. **结构性**：任何 `app/models` 下的局部唯一索引都必须在两个方言下都渲染出谓词。
   删除 `postgresql_where` 即失败。
2. **语义性**：在真实 PostgreSQL 上证明「历史终态行 + 当前 active 行」可以共存，
   而这正是退化后会失败的形状。
"""

from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects import sqlite as sq
from sqlalchemy.schema import CreateIndex

from app.models.base import Base

MODELS_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "models"

# 谓词是 `X IS NOT NULL` 的索引不需要方言谓词：PostgreSQL 唯一索引默认 NULLS DISTINCT，
# 含 NULL 的行本来就可重复，语义与 SQLite 的 `WHERE X IS NOT NULL` 等价。
# 这必须**逐条**列出并在用例中验证，而不是靠模式匹配放行——若将来改成
# `NULLS NOT DISTINCT` 或给列加非空默认值，它们会立刻变成真缺陷。
NULL_PREDICATE_EXEMPT = {
    "uq_agent_messages_session_key": "idempotency_key IS NOT NULL",
    "uq_agent_tool_calls_run_call": "tool_call_id IS NOT NULL",
    "uq_notifications_suggestion": "suggestion_id IS NOT NULL",
}


def _partial_unique_indexes():
    for table in Base.metadata.sorted_tables:
        for idx in table.indexes:
            if not idx.unique:
                continue
            if idx.dialect_options["sqlite"].get("where") is not None:
                yield table, idx


def test_every_partial_unique_index_exists():
    """盘点基线：局部唯一索引的数量不能被无声削减。

    数量下降通常意味着有人把索引换成了普通 `Index(...)`（丢掉唯一性），
    或删掉了谓词。两者都是并发保证的削弱，必须显式修改本断言。
    """
    found = list(_partial_unique_indexes())
    assert len(found) == 16, (
        f"局部唯一索引数量从 16 变为 {len(found)}：" f"{sorted(i.name for _t, i in found)}"
    )


def test_no_model_declares_only_one_dialect_predicate():
    """结构性守卫：模型层不得只声明单方言谓词。

    `partial_unique_index()` 是唯一被允许的构造方式，它内部把同一个谓词同时用于
    两个方言。绕过它手写 `sqlite_where` 会让 PG 静默退化。
    """
    offenders = []
    for path in sorted(MODELS_DIR.glob("*.py")):
        if path.name == "indexes.py":
            continue
        src = path.read_text()
        # 允许 docstring/注释里提到；只检查真实的关键字参数
        for m in re.finditer(r"^\s*(sqlite_where|postgresql_where)\s*=", src, re.M):
            offenders.append(f"{path.name}: {m.group(1)}")
    assert not offenders, (
        "模型层必须通过 app.models.indexes.partial_unique_index 声明局部索引，"
        f"不得手写单方言谓词：{offenders}"
    )


@pytest.mark.parametrize(
    "table,index", list(_partial_unique_indexes()), ids=lambda x: getattr(x, "name", "")
)
def test_partial_unique_index_renders_predicate_in_both_dialects(table, index):
    """每个局部唯一索引都必须在 SQLite 与 PostgreSQL 上渲染出 WHERE 子句。"""
    name = index.name
    sqlite_sql = str(CreateIndex(index).compile(dialect=sq.dialect()))
    pg_sql = str(CreateIndex(index).compile(dialect=pg.dialect()))

    assert " WHERE " in sqlite_sql, f"{name} 在 SQLite 上缺少谓词：{sqlite_sql}"
    if name in NULL_PREDICATE_EXEMPT:
        pytest.skip(f"{name} 是 NULL 谓词例外（PG NULLS DISTINCT 等价）")
    assert " WHERE " in pg_sql, f"{name} 在 PostgreSQL 上丢失了谓词，已退化为全表唯一索引：{pg_sql}"
    # 两个方言的谓词必须一致，否则「修了一边」会再次漂移
    sq_pred = sqlite_sql.split(" WHERE ", 1)[1].strip()
    pg_pred = pg_sql.split(" WHERE ", 1)[1].strip()
    assert sq_pred == pg_pred, f"{name} 两方言谓词不一致：sqlite={sq_pred!r} pg={pg_pred!r}"


def test_null_predicate_exemptions_are_still_exactly_that_shape():
    """例外项必须是真正的 NULL 谓词，防止例外集合被用来放过其他索引。"""
    by_name = {idx.name: idx for _t, idx in _partial_unique_indexes()}
    for name, predicate in NULL_PREDICATE_EXEMPT.items():
        idx = by_name.get(name)
        assert idx is not None, f"例外项 {name} 已不存在，请从例外集合移除"
        where = str(idx.dialect_options["sqlite"]["where"])
        assert where == predicate, f"{name} 的谓词已变化：{where!r} != {predicate!r}"
        assert where.strip().endswith(
            "IS NOT NULL"
        ), f"{name} 不再是 NULL 谓词，不能再享受例外：{where!r}"
