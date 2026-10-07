"""C6：PGroonga 分支 SQL 在**真实 PostgreSQL** 上执行（不是只查字符串形状）。

## 为什么要这一步

`tests/test_rag_search_provider.py` 只做子串断言（SQL 里有 `&@~`、有 eligibility）。
子串对不代表 SQL 能跑：探针第一次就发现 `eligibility` 的形态理解错了——
真实模板是 `WHERE ... AND {eligibility}`（**裸谓词**），而探针当时传了 `"AND ..."`，
拼出来是 `AND AND`（语法错误）。

因此必须有一步「把生成的 SQL 真的执行一次」。

## 本探针断言三件事

1. PGroonga 索引可在真实 PG 上建立（`PGROONGA_INDEX_DDL`）；
2. 带授权过滤的查询能执行且**撤权文档被过滤掉**；
3. **反证**：去掉过滤后同一查询能查到撤权文档——证明过滤条件**承重**，
   不是靠索引删除生效（索引不承载授权）。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pgroonga_branch_probe.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    override = os.environ.get("MIGRATION_PROOF_ROOT")
    if override:
        return Path(override)
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))

# 与 `memory_rag._ELIGIBILITY_SQL` 同形：**裸谓词**，由模板拼成
# `WHERE <condition> AND <eligibility>`。带前导 AND 会得到 `AND AND`（语法错误）。
ELIGIBILITY = "c.status = 'active' AND d.status = 'active'"

_HIT_SQL = (
    "SELECT c.id AS chunk_id FROM rag_chunks c"
    " JOIN rag_documents d ON d.id = c.document_id"
    " WHERE {condition} {eligibility} ORDER BY {ordering} LIMIT :limit OFFSET :offset"
)


def main() -> int:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2

    sys.path.insert(0, str(ROOT / "backend"))
    from sqlalchemy import create_engine, text as sa_text

    from app.services import rag_search_provider as provider

    engine = create_engine(dsn)
    failures: list[str] = []
    result: dict = {}

    with engine.begin() as conn:
        conn.execute(sa_text("DROP TABLE IF EXISTS rag_chunks, rag_documents CASCADE"))
        conn.execute(sa_text("""
            CREATE TABLE rag_documents (
              id serial primary key, source_type text, source_id text, scope text,
              sensitivity text, revision int, status text);
            CREATE TABLE rag_chunks (
              id serial primary key,
              document_id int references rag_documents(id),
              text text, token_estimate int, index_version text,
              chunk_index int, source_revision int, status text);
        """))
        conn.execute(sa_text(
            "INSERT INTO rag_documents"
            " (source_type,source_id,scope,sensitivity,revision,status) VALUES"
            " ('memory','m1','private','normal',1,'active'),"
            " ('memory','m2','private','normal',1,'revoked')"
        ))
        conn.execute(sa_text(
            "INSERT INTO rag_chunks"
            " (document_id,text,token_estimate,index_version,chunk_index,source_revision,status)"
            " VALUES (1,'叔叔和侄子',1,'v1',0,1,'active'),(2,'叔叔',1,'v1',0,1,'active')"
        ))
        # PGroonga 索引不在 ORM 元数据里，必须显式建（与 69 个触发器同类问题）
        conn.execute(sa_text(provider.PGROONGA_INDEX_DDL))

    with engine.connect() as conn:
        row = conn.execute(sa_text(
            "SELECT indexname FROM pg_indexes WHERE indexname='ix_rag_chunks_pgroonga'"
        )).fetchone()
        result["pgroonga_index_created"] = row is not None
        if row is None:
            failures.append("PGroonga 索引未建立")

        filtered = provider.build_lexical(
            "postgresql", match_terms=["叔叔"], fallback_terms=[],
            eligibility=ELIGIBILITY, hit_sql=_HIT_SQL,
        )[0]
        rows = conn.execute(
            filtered.sql, {**filtered.params, "limit": 10, "offset": 0}
        ).fetchall()
        result["filtered_hits"] = len(rows)
        print(f"  带过滤命中 {len(rows)} 行（期望 1：撤权文档被过滤）")
        if len(rows) != 1:
            failures.append(f"带过滤命中 {len(rows)} 行，期望 1")

        unfiltered = provider.build_lexical(
            "postgresql", match_terms=["叔叔"], fallback_terms=[],
            eligibility="1=1", hit_sql=_HIT_SQL,
        )[0]
        rows2 = conn.execute(
            unfiltered.sql, {**unfiltered.params, "limit": 10, "offset": 0}
        ).fetchall()
        result["unfiltered_hits"] = len(rows2)
        print(f"  反证（无过滤）命中 {len(rows2)} 行（期望 2：撤权文档可见）")
        if len(rows2) != 2:
            failures.append(
                f"反证未成立：无过滤命中 {len(rows2)} 行，期望 2——"
                "过滤条件可能不承重"
            )
        else:
            print("  OK  过滤条件承重（去掉它就能查到撤权内容）")

    with engine.begin() as conn:
        conn.execute(sa_text("DROP TABLE IF EXISTS rag_chunks, rag_documents CASCADE"))

    result["failures"] = failures
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pgroonga-branch.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: PGroonga 分支在真实 PostgreSQL 上可执行，且授权过滤承重")
    return 0


if __name__ == "__main__":
    sys.exit(main())
