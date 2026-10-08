"""守护：FTS5 投影的读写必须在 PostgreSQL 上被跳过。

## 为什么这是安全要求

`rag_chunks_fts` 是 **SQLite FTS5 虚拟表**，PostgreSQL 上不存在。无守卫地写它
会让**任何记忆索引都失败**：

```text
psycopg.errors.UndefinedTable: relation "rag_chunks_fts" does not exist
```

实测（生产，2026-10-08）：RAG 从未有过内容，这条路径从未被执行，缺陷一直隐藏；
写入第一条记忆时立刻暴露。PostgreSQL 侧的中文词法检索由 PGroonga 承担
（`rag_search_provider.build_lexical` 已按方言分派），不需要 FTS5 投影。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_PATH = Path(__file__).resolve().parents[1] / "app/services/memory_rag.py"


def _function_source(name: str) -> str:
    tree = ast.parse(SRC_PATH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC_PATH.read_text(), node) or ""
    raise AssertionError(f"{name} 不存在")


def test_fts5_write_paths_are_dialect_guarded() -> None:
    """`_repair_chunk_fts` 与 `repair_fts` 必须在写 FTS5 前检查方言。"""
    for name in ("_repair_chunk_fts", "repair_fts"):
        body = _function_source(name)
        assert "_is_sqlite" in body, (
            f"{name} 未做方言检查——在 PostgreSQL 上会写不存在的 rag_chunks_fts 表，"
            "使任何记忆索引都失败"
        )


def test_fts_rows_read_is_dialect_guarded() -> None:
    """`_fts_rows` 读 FTS5 也必须守卫（否则读取阶段就先炸）。"""
    body = _function_source("_fts_rows")
    assert "_is_sqlite" in body, "_fts_rows 未做方言检查"


def test_fts5_literal_only_appears_inside_guarded_functions() -> None:
    """`rag_chunks_fts` 字面量只允许出现在已守卫的函数里（或注释/文档）。"""
    tree = ast.parse(SRC_PATH.read_text())
    allowed = {"_is_sqlite", "_fts_rows", "_repair_chunk_fts", "repair_fts"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if "rag_chunks_fts" in sub.value and node.name not in allowed:
                    offenders.append(f"{node.name}:{sub.lineno}")
    assert not offenders, (
        "未守卫的函数里出现 rag_chunks_fts 字面量（PostgreSQL 上会失败）：\n  "
        + "\n  ".join(offenders)
    )
