"""RAG 词法检索的**方言抽象**（C6）。

## 为什么需要这一层

现有检索直接写 `rag_chunks_fts MATCH ... bm25(...)`——那是 SQLite FTS5 专属语法，
PostgreSQL 上既没有虚拟表也没有 `bm25()`。直接翻译不行：四方对照实测（见
`10-04-lexical-search-migration` 的证据）表明

| 方案 | 短查询 | 精确性 |
|---|---|---|
| **PGroonga** | 可用 | **10/10 精确** |
| Unicode n-gram（应用维护） | 可用 | 3/10（过度召回） |
| FTS5 + LIKE | 可用 | 10/10 但无排序 |
| `pg_trgm` | **不可用** | 0/10（CJK 相似度全部低于阈值） |

因此 PostgreSQL 走 PGroonga，SQLite 保持 FTS5。

## 安全边界（本层最重要的一点）

**授权过滤不在这层**。`eligibility` 谓词由调用方（`search_rag`）传入并原样拼进
两种方言的 SQL。理由：检索索引不承载授权——实测撤权只改主表状态，索引条目仍在
（PGroonga 与 pgvector 都确认过）。可见性**完全**依赖查询层过滤。

因此本模块**只**决定「怎么找候选」，绝不决定「谁能看」。任何在这里放宽或跳过
`eligibility` 的改动都是授权漏洞。

## 候选预算

两种方言共享同一个 `_FALLBACK_SCAN_LIMIT`：这是**返回候选数**的上界，不是数据库
内部扫描行数的上界（PGroonga 的内部扫描不在 SQL 层可见）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


@dataclass(frozen=True)
class LexicalQuery:
    """一次词法检索的 SQL 与参数。

    `rank_by_order`：SQL 已按相关度排序时为 False（沿用行序），否则为 True
    （`_rows_to_hits` 用行序当排名）。两种方言的排序能力不同，因此必须显式声明，
    不能假设。
    """

    sql: Any
    params: dict[str, Any]
    rank_by_order: bool


def supports_lexical(dialect_name: str) -> bool:
    """该方言是否支持有索引的词法检索。"""
    return dialect_name in {"sqlite", "postgresql"}


def _fts5_match(value: str) -> str:
    """把用户输入包成 FTS5 短语，避免被当作查询语法。

    FTS5 的 `MATCH` 接受自己的查询语法（`AND`/`OR`/`*`/`NEAR` 等）。把输入直接
    拼进去会让用户输入变成语法，因此整体加引号当短语；内部的双引号按 FTS5 规则
    用两个双引号转义。
    """
    return '"' + value.replace('"', '""') + '"'


def build_sqlite_lexical(
    *,
    match_terms: list[str],
    fallback_terms: list[str],
    eligibility: str,
    hit_sql: str,
) -> list[LexicalQuery]:
    """SQLite：FTS5 `MATCH` + `bm25` 排序；短词用参数化 LIKE 后备。

    后备是必要的：FTS5 的 trigram 分词器无法匹配少于 3 个字符的词，而中文里
    两字词（"叔叔"、"外祖父"）非常常见。
    """
    out: list[LexicalQuery] = []
    if match_terms:
        sql = text(f"""
            SELECT c.id AS chunk_id, d.id AS document_id, d.source_type, d.source_id, c.text,
                   c.token_estimate, d.scope, d.sensitivity, d.revision, c.index_version,
                   c.chunk_index, c.source_revision, bm25(rag_chunks_fts) AS rank
            FROM rag_chunks_fts
            JOIN rag_chunks AS c ON c.id = rag_chunks_fts.rowid
            JOIN rag_documents AS d ON d.id = c.document_id
            WHERE rag_chunks_fts MATCH :match AND {eligibility}
            ORDER BY rank ASC, c.id ASC LIMIT :limit OFFSET :offset
        """)
        out.append(
            LexicalQuery(
                sql=sql,
                params={"match": " OR ".join(_fts5_match(t) for t in match_terms)},
                rank_by_order=False,
            )
        )
    if fallback_terms:
        clauses = " OR ".join(
            f"c.text LIKE :like{i} ESCAPE '!'" for i in range(len(fallback_terms))
        )
        # LIKE 的模式字符必须转义，否则字面量下划线会匹配无关文本。
        escaped = [
            term.replace("!", "!!").replace("%", "!%").replace("_", "!_") for term in fallback_terms
        ]
        sql = text(
            hit_sql.format(condition=f"({clauses})", eligibility=eligibility, ordering="c.id ASC")
        )
        out.append(
            LexicalQuery(
                sql=sql,
                params={f"like{i}": f"%{t}%" for i, t in enumerate(escaped)},
                rank_by_order=True,
            )
        )
    return out


def build_postgres_lexical(
    *,
    match_terms: list[str],
    fallback_terms: list[str],
    eligibility: str,
    hit_sql: str,
) -> list[LexicalQuery]:
    """PostgreSQL：PGroonga `&@~` 运算符 + 其自带相关度排序。

        ## 与 SQLite 分支的三点差异（都是实测结论，不是风格）

    1. **没有虚拟表**：PGroonga 是 `rag_chunks.text` 上的一个索引，查询直接对表，
       不需要 `rag_chunks_fts` 也没有 `bm25()`。相关度由 `pgroonga_score(tableoid, ctid)`
       给出。
    2. **不需要短词后备**：PGroonga 用 Groonga 的分词，两字中文词正常匹配（实测
       短查询可用）。因此 `fallback_terms` 在 PG 分支被合并进主查询，而不是另起 LIKE。
    3. **`&@~` 是查询语法运算符**：它接受 Groonga 查询语法。为避免用户输入变成语法，
       对每个词加双引号（Groonga 的短语语法），与 FTS5 的处理同源。

    `ORDER BY score DESC` 必须显式写：PGroonga 只提供分数，不替调用方排序。
    """
    terms = list(dict.fromkeys([*match_terms, *fallback_terms]))  # 去重保序
    if not terms:
        return []
    # Groonga 短语语法：双引号内的内容按字面短语匹配。
    quoted = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    sql = text(f"""
        SELECT c.id AS chunk_id, d.id AS document_id, d.source_type, d.source_id, c.text,
               c.token_estimate, d.scope, d.sensitivity, d.revision, c.index_version,
               c.chunk_index, c.source_revision,
               pgroonga_score(c.tableoid, c.ctid) AS rank
        FROM rag_chunks AS c
        JOIN rag_documents AS d ON d.id = c.document_id
        WHERE c.text &@~ :match AND {eligibility}
        ORDER BY rank DESC, c.id ASC LIMIT :limit OFFSET :offset
    """)
    return [
        LexicalQuery(sql=sql, params={"match": quoted}, rank_by_order=False),
    ]


def build_lexical(
    dialect_name: str,
    *,
    match_terms: list[str],
    fallback_terms: list[str],
    eligibility: str,
    hit_sql: str,
) -> list[LexicalQuery]:
    """按方言分派。未知方言 fail-loud——静默降级会隐藏「检索没接上」。"""
    if dialect_name == "sqlite":
        return build_sqlite_lexical(
            match_terms=match_terms,
            fallback_terms=fallback_terms,
            eligibility=eligibility,
            hit_sql=hit_sql,
        )
    if dialect_name == "postgresql":
        return build_postgres_lexical(
            match_terms=match_terms,
            fallback_terms=fallback_terms,
            eligibility=eligibility,
            hit_sql=hit_sql,
        )
    raise ValueError(f"unsupported dialect for lexical search: {dialect_name}")


#: PostgreSQL baseline 需要为词法检索建立的索引。
#:
#: 必须在 baseline 里显式创建——它不在 ORM 元数据里（PGroonga 是扩展索引），
#: 因此 `create_all` 看不到它，与 69 个触发器同一类问题。
PGROONGA_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_rag_chunks_pgroonga " "ON rag_chunks USING pgroonga (text)"
)
