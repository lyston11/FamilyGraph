"""`save_target` 写 `json` 列的绑定必须按方言分派。

## 为什么这是安全要求（实测生产故障）

`save_target` 的入参是已 `json.dumps` 过的字符串，两类方言对它的要求**相反**：

- **SQLite**：必须 `Text()` 绑定。用 `JSON()` 绑定会**双重编码**（列里存字符串而不是
  对象）——这正是原注释警告的问题。
- **PostgreSQL**：`json` 列拒绝 `character varying` 表达式：

  ```text
  psycopg.errors.DatatypeMismatch: column "resolution_json" is of type json
    but expression is of type character varying
  ```

实测（生产 2026-10-08）：这个不匹配使空间 1/2 的 job 持续
`STEWARD_EXECUTION_FAILED`。它**只在搜索找到路径时**触发（只有 `found: true` 才写
`resolution_json`），因此症状是「少数人查不到」，而不是整体故障——极易被误判为数据
问题或上游问题。`steward_runtime` 当时也不带 traceback，所以日志只有
`error=RequiredTargetFailed`，直到补上 `exc_info` 才看见真实类型错误。
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import JSON, Text, create_engine
from sqlalchemy.dialects import postgresql, sqlite

from app.services.steward_pipeline import _json_text_bind

SRC = Path(__file__).resolve().parents[1] / "app/services/steward_pipeline.py"


def _compile(bind, dialect) -> str:
    expr = _json_text_bind(bind, "doc", '{"a":1}')
    return str(expr.compile(dialect=dialect))


def test_postgresql_casts_the_payload_to_json():
    """PG 上必须渲染为 `CAST(... AS JSON)`，否则 json 列拒绝 varchar。"""
    bind = create_engine("postgresql+psycopg://u:p@h/db")
    sql = _compile(bind, postgresql.dialect())
    assert "CAST" in sql.upper(), f"未做 CAST：{sql}"
    assert "JSON" in sql.upper(), f"未转成 JSON：{sql}"


def test_sqlite_keeps_a_plain_text_bind():
    """SQLite 上必须是普通 Text 绑定（不能 CAST，SQLite 会求值为整数 0）。"""
    bind = create_engine("sqlite://")
    sql = _compile(bind, sqlite.dialect())
    assert (
        "CAST" not in sql.upper()
    ), f"SQLite 上出现了 CAST：{sql}——SQLite 的 CAST(x AS JSON) 会求值为整数 0"


def test_save_target_uses_the_dialect_aware_bind():
    """`save_target` 必须用该 helper，而不是硬编码 `type_=Text()`。"""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "save_target":
            body = ast.get_source_segment(SRC.read_text(), node) or ""
            assert "_json_text_bind(" in body, "save_target 未使用方言感知的绑定"
            assert (
                "type_=Text()" not in body
            ), "save_target 仍在硬编码 Text 绑定——PostgreSQL 的 json 列会拒绝"
            return
    raise AssertionError("save_target 不存在")


def test_helper_returns_text_bind_on_sqlite_and_cast_on_pg():
    """helper 本身在两个方言上的类型选择。"""
    pg = _json_text_bind(create_engine("postgresql+psycopg://u:p@h/db"), "doc", "{}")
    lite = _json_text_bind(create_engine("sqlite://"), "doc", "{}")
    # PG 分支是 Cast 表达式（不是普通 BindParameter）。
    assert type(pg).__name__ != type(lite).__name__
    assert isinstance(lite, type(lite)) and not isinstance(pg, type(lite))
    # 两者都不能用 JSON 绑定类型（那会双重编码）。
    assert not isinstance(lite.type, JSON)
    assert isinstance(lite.type, Text)
