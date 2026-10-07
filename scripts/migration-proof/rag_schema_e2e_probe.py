"""C6：在**真实 88 表 schema** 上端到端验证 PGroonga 检索与授权过滤。

## 为什么与 `pgroonga_branch_probe.py` 分开

后者用两张最小表验证「生成的 SQL 能执行」。本探针验证的是**真实业务 schema**：
`rag_documents` / `rag_chunks` 的真实列（含 NOT NULL 的 `embedding_status` 等），
以及真实 `_ELIGIBILITY_SQL` 形态的过滤条件。

两者的失败含义不同：前者失败说明 provider 写错了 SQL；后者失败说明真实 schema
上有 provider 没考虑到的约束（实测就是这样——第一次跑就撞上
`embedding_status` NOT NULL）。

用法：

    PGTEST_DSN=postgresql+psycopg://... python3 scripts/migration-proof/rag_schema_e2e_probe.py
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

#: 与 `memory_rag._ELIGIBILITY_SQL` 同形：**裸谓词**。
ELIGIBILITY = "c.status = 'active' AND d.status = 'active'"

_HIT_SQL = (
    "SELECT c.id AS chunk_id FROM rag_chunks c"
    " JOIN rag_documents d ON d.id = c.document_id"
    " WHERE {condition} {eligibility} ORDER BY {ordering} LIMIT :limit OFFSET :offset"
)


def main() -> int:
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2
    try:
        from sqlalchemy import create_engine, text as sa_text
    except ImportError:
        print("SKIP: 需要 sqlalchemy")
        return 2

    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.setdefault("DATA_DIR", "/tmp/fg-rag-e2e")
    from app.services import rag_search_provider as provider

    engine = create_engine(dsn)
    failures: list[str] = []
    result: dict = {}

    try:
        with engine.begin() as conn:
            conn.execute(sa_text(
                "INSERT INTO users (id,name,gender,privacy_mode,profile_status,created_at)"
                " VALUES (9001,'e2e','unknown','perpetual','identity_confirmed',CURRENT_TIMESTAMP)"
                " ON CONFLICT (id) DO NOTHING"
            ))
            conn.execute(sa_text(
                "INSERT INTO family_spaces (id,name,owner_id,kind,created_at)"
                " VALUES (9001,'e2e',9001,'household',CURRENT_TIMESTAMP) ON CONFLICT (id) DO NOTHING"
            ))
            # 真实 schema 的 NOT NULL 列必须全部提供：实测第一次跑就撞上
            # `embedding_status` NOT NULL——最小表探针发现不了这类约束。
            conn.execute(sa_text("""
                INSERT INTO rag_documents (id,source_type,source_id,revision,source_revision,scope,
                    sensitivity,confirmation_status,visibility_snapshot,visibility_snapshot_key,
                    index_version,status,created_at,updated_at)
                VALUES (9101,'memory','m1',1,1,'private','normal','confirmed','{}','k','v1','active',
                        CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),
                       (9102,'memory','m2',1,1,'private','normal','confirmed','{}','k','v1','revoked',
                        CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO NOTHING
            """))
            conn.execute(sa_text("""
                INSERT INTO rag_chunks (id,document_id,text,token_estimate,index_version,chunk_index,
                    source_revision,status,embedding_status,created_at,updated_at)
                VALUES (9201,9101,'叔叔和侄子',1,'v1',0,1,'active','pending',
                        CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),
                       (9202,9102,'叔叔',1,'v1',0,1,'active','pending',
                        CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO NOTHING
            """))
            conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS pgroonga"))
            conn.execute(sa_text(provider.PGROONGA_INDEX_DDL))

        with engine.connect() as conn:
            filtered = provider.build_lexical(
                "postgresql", match_terms=["叔叔"], fallback_terms=[],
                eligibility=ELIGIBILITY, hit_sql=_HIT_SQL,
            )[0]
            rows = [r[0] for r in conn.execute(
                filtered.sql, {**filtered.params, "limit": 10, "offset": 0}
            ).fetchall()]
            result["filtered"] = rows
            print(f"  真实 schema 带过滤命中 {rows}（期望 [9201]）")
            if rows != [9201]:
                failures.append(f"真实 schema 检索结果 {rows}，期望 [9201]")

            unfiltered = provider.build_lexical(
                "postgresql", match_terms=["叔叔"], fallback_terms=[],
                eligibility="1=1", hit_sql=_HIT_SQL,
            )[0]
            rows2 = [r[0] for r in conn.execute(
                unfiltered.sql, {**unfiltered.params, "limit": 10, "offset": 0}
            ).fetchall()]
            result["unfiltered"] = rows2
            print(f"  反证（无过滤）命中 {rows2}（期望含 9202）")
            if 9202 not in rows2:
                failures.append("反证未成立：无过滤时撤权文档仍不可见")
            else:
                print("  OK  授权过滤承重（去掉它就能查到撤权内容）")
    finally:
        try:
            with engine.begin() as conn:
                conn.execute(sa_text(
                    "DELETE FROM rag_chunks WHERE id IN (9201,9202)"
                ))
                conn.execute(sa_text(
                    "DELETE FROM rag_documents WHERE id IN (9101,9102)"
                ))
        except Exception:  # noqa: BLE001
            pass

    result["failures"] = failures
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "rag-schema-e2e.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 真实 88 表 schema 上 PGroonga 检索可用，授权过滤承重")
    return 0


if __name__ == "__main__":
    sys.exit(main())
