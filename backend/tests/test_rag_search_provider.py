"""C6：RAG 词法检索的方言分派。

## 本文件证明什么

1. **方言分派正确**：SQLite 出 FTS5 SQL，PostgreSQL 出 PGroonga SQL，未知方言 fail-loud；
2. **授权过滤原样传入**：`eligibility` 必须出现在两种方言的 SQL 里——检索索引不承载
   授权（撤权只改主表状态，索引条目仍在），因此可见性**完全**依赖这个谓词；
3. **参数化**：用户输入不得拼进 SQL 文本（Groonga/FTS5 都有查询语法，拼接会让输入
   变成语法）；
4. **短词后备的差异**：SQLite 需要 LIKE 后备（trigram 无法匹配 <3 字符），
   PGroonga 不需要（实测短查询可用）。

## 为什么这些是结构性断言而不是语义断言

真正的检索语义（召回/排序/撤权）已在 `10-04`/`10-03` 的真实数据库基准里验证。
本文件守护的是**接线**：方言分派与过滤条件必须出现在生成的 SQL 里，否则
PostgreSQL 上要么语法错误、要么（更糟）少了授权过滤。
"""

from __future__ import annotations

import pytest

from app.services import rag_search_provider as provider

# 真实形态：`_ELIGIBILITY_SQL` 是**裸谓词**（模板里写成 `WHERE ... AND {eligibility}`），
# 不带前导 AND。单测必须用同一形态，否则拼出来的 SQL 是 `AND AND`（语法错误）。
_ELIGIBILITY = "c.status = 'active' AND d.status = 'active' AND d.scope = 'private'"
_HIT_SQL = (
    "SELECT c.id AS chunk_id FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id"
    " WHERE {condition} {eligibility} ORDER BY {ordering} LIMIT :limit OFFSET :offset"
)


def test_sqlite_uses_fts5_and_bm25():
    queries = provider.build_lexical(
        "sqlite",
        match_terms=["叔叔"],
        fallback_terms=[],
        eligibility=_ELIGIBILITY,
        hit_sql=_HIT_SQL,
    )
    assert len(queries) == 1
    sql = str(queries[0].sql)
    assert "rag_chunks_fts" in sql, "SQLite 分支必须用 FTS5 虚拟表"
    assert "bm25(" in sql, "SQLite 分支必须用 bm25 排序"
    assert _ELIGIBILITY in sql, "授权过滤缺失——这是授权漏洞，不是风格问题"
    assert queries[0].rank_by_order is False, "bm25 已排序，不应再按行序排名"


def test_sqlite_adds_like_fallback_for_short_terms():
    """trigram 无法匹配 <3 字符，因此短词必须走参数化 LIKE 后备。"""
    queries = provider.build_lexical(
        "sqlite",
        match_terms=[],
        fallback_terms=["叔叔", "外祖父"],
        eligibility=_ELIGIBILITY,
        hit_sql=_HIT_SQL,
    )
    assert len(queries) == 1
    sql = str(queries[0].sql)
    assert "LIKE :like0" in sql and "LIKE :like1" in sql
    assert "ESCAPE '!'" in sql, "LIKE 模式字符未转义"
    assert _ELIGIBILITY in sql
    assert queries[0].rank_by_order is True, "LIKE 无相关度，需按行序排名"


def test_sqlite_escapes_like_pattern_characters():
    """字面量 `_`/`%`/`!` 必须转义，否则会匹配无关文本。"""
    queries = provider.build_lexical(
        "sqlite",
        match_terms=[],
        fallback_terms=["a_b%c!d"],
        eligibility=_ELIGIBILITY,
        hit_sql=_HIT_SQL,
    )
    assert queries[0].params["like0"] == "%a!_b!%c!!d%"


def test_postgres_uses_pgroonga_and_score():
    queries = provider.build_lexical(
        "postgresql",
        match_terms=["叔叔"],
        fallback_terms=[],
        eligibility=_ELIGIBILITY,
        hit_sql=_HIT_SQL,
    )
    assert len(queries) == 1
    sql = str(queries[0].sql)
    assert "&@~" in sql, "PostgreSQL 分支必须用 PGroonga 运算符"
    assert "pgroonga_score" in sql, "PostgreSQL 分支必须用 PGroonga 相关度"
    assert "rag_chunks_fts" not in sql, "PostgreSQL 没有 FTS5 虚拟表"
    assert "bm25(" not in sql, "PostgreSQL 没有 bm25()"
    assert _ELIGIBILITY in sql, "授权过滤缺失——这是授权漏洞"
    assert queries[0].rank_by_order is False


def test_postgres_merges_fallback_into_the_main_query():
    """PGroonga 不需要短词后备：两字中文词正常匹配（实测）。

    因此 fallback_terms 被**合并**进主查询，而不是另起一个 LIKE 分支——
    另起 LIKE 会失去索引，退化成全表扫描。
    """
    queries = provider.build_lexical(
        "postgresql",
        match_terms=["祖父"],
        fallback_terms=["叔叔"],
        eligibility=_ELIGIBILITY,
        hit_sql=_HIT_SQL,
    )
    assert len(queries) == 1, "PG 分支不应产生第二个 LIKE 查询"
    sql = str(queries[0].sql)
    assert "LIKE" not in sql, "PG 分支不得退化为 LIKE 全表扫描"
    match_value = queries[0].params["match"]
    assert "祖父" in match_value and "叔叔" in match_value


def test_postgres_quotes_terms_to_avoid_query_syntax_injection():
    """Groonga 的 `&@~` 接受查询语法，用户输入必须被当作字面短语。"""
    queries = provider.build_lexical(
        "postgresql",
        match_terms=["a OR b"],
        fallback_terms=[],
        eligibility=_ELIGIBILITY,
        hit_sql=_HIT_SQL,
    )
    value = queries[0].params["match"]
    assert value.startswith('"') and value.endswith('"'), f"未加短语引号：{value!r}"


def test_sqlite_quotes_terms_to_avoid_fts_syntax_injection():
    """FTS5 的 MATCH 也接受查询语法（AND/OR/NEAR），必须按短语处理。"""
    queries = provider.build_lexical(
        "sqlite",
        match_terms=["a OR b"],
        fallback_terms=[],
        eligibility=_ELIGIBILITY,
        hit_sql=_HIT_SQL,
    )
    assert queries[0].params["match"] == '"a OR b"'


def test_unknown_dialect_fails_loud():
    """未知方言必须报错，不得静默降级——静默降级会隐藏「检索没接上」。"""
    with pytest.raises(ValueError, match="unsupported dialect"):
        provider.build_lexical(
            "mysql",
            match_terms=["x"],
            fallback_terms=[],
            eligibility=_ELIGIBILITY,
            hit_sql=_HIT_SQL,
        )


def test_empty_terms_produce_no_queries():
    """无词项时不产生查询（避免无条件全表扫描）。"""
    for dialect in ("sqlite", "postgresql"):
        assert (
            provider.build_lexical(
                dialect,
                match_terms=[],
                fallback_terms=[],
                eligibility=_ELIGIBILITY,
                hit_sql=_HIT_SQL,
            )
            == []
        )


def test_pgroonga_index_ddl_is_explicit():
    """PGroonga 索引不在 ORM 元数据里，必须显式建。

    与 69 个触发器同类问题：`create_all` 走元数据，看不到扩展索引，因此
    baseline 必须显式包含它，否则 PostgreSQL 上检索会退化为顺序扫描。
    """
    ddl = provider.PGROONGA_INDEX_DDL
    assert "USING pgroonga" in ddl
    assert "rag_chunks" in ddl
    assert "IF NOT EXISTS" in ddl, "重复执行必须幂等"
