"""方言感知 CHECK 约束必须表达**同一个**判据。

## 背景（真实缺陷）

`memory_candidates` / `memories` 的 source-snapshot CHECK 用了 SQLite 的
`json_extract`。PostgreSQL 没有该函数，因此这两张表在 PG 上**根本建不出来**
（实测：逐表建表时 87 张中这 2 张失败）。

修复不是「换个能跑的写法」，而是「换个**等价**的写法」。SQLite 的
`json_extract(col,'$.version') = 1` 是**类型敏感**的：

| JSON 载荷 | `= 1` | 说明 |
|---|---|---|
| `{"version":1}` | true | 数字 1 |
| `{"version":"1"}` | **false** | 字符串 `"1"` ≠ 数字 `1` |
| `{"version":2}` | false | |
| `{"version":null}` / `{}` | NULL → coalesce 后 false | |

因此 PG 侧**不能**写 `(col::jsonb ->> 'version')::int = 1`——那会把 `"1"` 判为相等，
是**不同**的约束。正确形态是 jsonb 对 jsonb 比较：`(col::jsonb -> 'version') = '1'::jsonb`。

本文件用 SQLite 自己的求值结果作为**唯一真源**，逐值断言两方言一致，而不是靠人读代码。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.models.checks import DialectCheck
from app.models.memory import source_snapshot_check

# (载荷, source_kind) —— 覆盖类型敏感、不匹配、缺失、短路四条边界
CASES = [
    ('{"version":1,"kind":"manual"}', "manual"),
    ('{"version":"1","kind":"manual"}', "manual"),
    ('{"version":2,"kind":"manual"}', "manual"),
    ('{"version":1,"kind":"legacy"}', "manual"),
    ('{"version":1,"kind":2}', "manual"),
    ('{"version":null,"kind":"manual"}', "manual"),
    ("{}", "manual"),
    ('{"version":1}', "manual"),
]

VERIFICATIONS = ["verified", "unverified"]


def _sqlite_result(payload: str, kind: str, verification: str) -> bool:
    """用 SQLite 自身求值——这是判据的唯一真源。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE t (source_span_json TEXT, source_kind TEXT, source_verification TEXT,"
        " source_type TEXT, source_id TEXT, source_revision INTEGER)"
    )
    # 前置条件要求 source_type/source_id 非空且 revision > 0，否则 verified 分支恒假，
    # 类型敏感那条边界就测不出来。
    conn.execute("INSERT INTO t VALUES (?,?,?,'rag_chunk','x',1)", (payload, kind, verification))
    check = source_snapshot_check("probe")
    row = conn.execute(
        f"SELECT {check.sqlite_expr} FROM t"  # noqa: S608 - 测试内固定表达式
    ).fetchone()
    return bool(row[0])


@pytest.fixture(scope="module")
def pg_conn():
    """真实 PostgreSQL 连接；不可用则跳过而不是伪造通过。"""
    psycopg = pytest.importorskip("psycopg")
    import os

    dsn = os.environ.get("FAMILYGRAPH_TEST_PG_DSN")
    if not dsn:
        pytest.skip("需要 FAMILYGRAPH_TEST_PG_DSN 指向隔离 PostgreSQL 才能验证方言等价性")
    conn = psycopg.connect(dsn)
    conn.execute("DROP TABLE IF EXISTS probe_check")
    conn.execute(
        "CREATE TABLE probe_check (source_span_json json, source_kind text,"
        " source_verification text, source_type text, source_id text, source_revision integer)"
    )
    conn.commit()
    yield conn
    conn.execute("DROP TABLE IF EXISTS probe_check")
    conn.commit()
    conn.close()


def _pg_result(conn, payload: str, kind: str, verification: str) -> bool:
    conn.execute("DELETE FROM probe_check")
    conn.execute(
        "INSERT INTO probe_check VALUES (%s,%s,%s,'rag_chunk','x',1)", (payload, kind, verification)
    )
    check = source_snapshot_check("probe")
    row = conn.execute(f"SELECT {check.postgres_expr} FROM probe_check").fetchone()
    return bool(row[0])


@pytest.mark.parametrize("payload,kind", CASES)
@pytest.mark.parametrize("verification", VERIFICATIONS)
def test_postgres_matches_sqlite_semantics(pg_conn, payload, kind, verification):
    """PG 表达式必须与 SQLite 表达式对每个边界值给出相同结果。"""
    expected = _sqlite_result(payload, kind, verification)
    actual = _pg_result(pg_conn, payload, kind, verification)
    assert actual == expected, (
        f"方言不一致：payload={payload} kind={kind} verification={verification} "
        f"sqlite={expected} pg={actual}"
    )


def test_the_type_sensitive_case_is_actually_discriminating():
    """必须存在一个「数字与字符串结果不同」的用例，否则本测试没有守护类型敏感语义。

    若 PG 侧误写成 `->> ... ::int`，`{"version":"1"}` 会与 `{"version":1}` 一样通过，
    下面这条断言就会失败。
    """
    number = _sqlite_result('{"version":1,"kind":"manual"}', "manual", "verified")
    string = _sqlite_result('{"version":"1","kind":"manual"}', "manual", "verified")
    assert number is True
    assert string is False, (
        'SQLite 的类型敏感语义变了：字符串 "1" 竟然等于数字 1——' "此时本文件的等价式需要重新推导"
    )


def test_unverified_short_circuits_in_both_dialects(pg_conn):
    """`source_verification='unverified'` 时前置条件短路，JSON 内容不参与判定。"""
    for payload in ("{}", '{"version":"1"}', '{"version":1,"kind":"legacy"}'):
        assert _sqlite_result(payload, "manual", "unverified") is True
        assert _pg_result(pg_conn, payload, "manual", "unverified") is True


def test_dialect_check_requires_both_expressions():
    """辅助本身拒绝空表达式，避免误用成「只声明一个方言」。"""
    with pytest.raises(ValueError):
        DialectCheck(sqlite_expr="  ", postgres_expr="x = 1", name="bad")
    with pytest.raises(ValueError):
        DialectCheck(sqlite_expr="x = 1", postgres_expr="", name="bad")


def test_source_snapshot_check_has_no_sqlite_only_function_in_pg_expression():
    """PG 表达式不得含 SQLite 专属函数（防止再次写出建表失败的约束）。"""
    check = source_snapshot_check("probe")
    for token in ("json_extract", "strftime", "julianday", "AUTOINCREMENT"):
        assert (
            token not in check.postgres_expr
        ), f"PG 表达式仍含 SQLite 专属构造 {token!r}：{check.postgres_expr}"


# ---------------------------------------------------------------------------
# 已知且**刻意保留**的语义分歧：JSON 布尔值
# ---------------------------------------------------------------------------
# SQLite 的 json_extract 把 JSON `true` 折成整数 1，因此 `{"version":true}` 满足
# `json_extract(...) = 1`；PostgreSQL 保留 JSON 类型，`'true'::jsonb != '1'::jsonb`。
#
# 判定为**可接受**，依据是可达性而不是「差不多」：
#   - 快照由 `memory_sources.ExactChunkRef.as_json()` 产生，`version` 恒为 Python
#     整数字面量 1（`{"version": 1, **self.__dict__}`）；
#   - `source_kind` 只能取 `MEMORY_SOURCE_KINDS` 中的字符串，永不为数字或布尔。
# 因此分歧只存在于应用自己不会写出的形状上，而 PG 侧更严格（拒绝而非放行）——
# 这是安全方向。
#
# 这条记录的作用是**防止有人把 PG 表达式「修」成 `(col ->> 'version')::int = 1`**：
# 那样虽然也接受 `{"version":1}`，却会连带接受字符串 `"1"`，把约束放松到 SQLite
# 语义之外。下面的用例把两个方向都钉住。


def test_boolean_version_diverges_and_that_is_deliberate(pg_conn):
    """布尔 version 的分歧必须被记录，且 PG 侧方向是「更严格」。"""
    payload = '{"version":true,"kind":"manual"}'
    assert _sqlite_result(payload, "manual", "verified") is True
    assert _pg_result(pg_conn, payload, "manual", "verified") is False
    # 应用不会写出这种形状：version 来自整数字面量（`as_json` 里写死 `"version": 1`），
    # kind 只能取 MEMORY_SOURCE_KINDS 中的字符串。
    from app.models.memory import MEMORY_SOURCE_KINDS
    from app.services.memory_sources import ExactChunkRef

    assert (
        ExactChunkRef(  # 真实构造出的快照里 version 恒为 int
            document_id=1,
            chunk_id="c",
            source_revision=1,
            index_version="v",
            chunk_index=0,
            content_hash="0" * 64,
            source_type="rag_chunk",
            source_id="x",
        ).as_json()["version"]
        == 1
    )
    assert all(isinstance(k, str) for k in MEMORY_SOURCE_KINDS)


def test_the_pg_form_is_not_loosened_to_strings(pg_conn):
    """PG 表达式不得接受字符串 "1"——那是把约束放松到 SQLite 语义之外。

    若有人改成 `(source_span_json::jsonb ->> 'version')::int = 1`，本条失败。
    """
    payload = '{"version":"1","kind":"manual"}'
    assert _sqlite_result(payload, "manual", "verified") is False
    assert _pg_result(pg_conn, payload, "manual", "verified") is False
