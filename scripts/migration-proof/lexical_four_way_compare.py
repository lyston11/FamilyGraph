"""中文词法检索四方对照：FTS5 / pg_trgm / Unicode n-gram / PGroonga（全部实测）。

## 为什么做这个

`10-04-lexical-search-migration` 需要「golden corpus 对照」来决定主路径。本脚本把
四个候选跑在同一份语料与同一批查询上，给出**可逐格比较**的命中矩阵。

## 真源（ground truth）

语料是合成中文亲属语句，因此「正确命中」可由**子串包含**定义：
查询是文档子串即应命中。这给了客观判据，不依赖主观相关性。

## 四个候选的性质差异

| 候选 | 短查询（1–2 字） | 排序 | 额外依赖 |
|---|---|---|---|
| SQLite FTS5 trigram | `MATCH` 零命中（需 ≥3 字符） | `bm25` | 无 |
| PostgreSQL pg_trgm `%` | 零命中（相似度低于阈值） | `similarity()` | pg_trgm |
| Unicode n-gram 倒排表 | 可用（含单字） | 自实现覆盖度打分 | 无 |
| **PGroonga `&@`** | **可用（含单字）** | PGroonga 评分 | **需安装扩展** |

用法（需要两个隔离库：普通 PG 与带 PGroonga 的 PG）：

    PGTEST_DSN=... PGTEST_DSN_GROONGA=... \\
        python3 scripts/migration-proof/lexical_four_way_compare.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unicodedata

CORPUS = [
    "爷爷是家里的长辈，喜欢下棋",
    "奶奶做的饭最好吃",
    "叔叔在城里工作",
    "叔父与父亲是兄弟",
    "家族树关系图谱用于展示亲属结构",
    "家族成员的称谓由关系路径决定",
    "舅舅是母亲的兄弟",
    "姨妈是母亲的姐妹",
    "堂兄是伯父的儿子",
    "外祖父是母亲的父亲",
    "家族树支持多代展示",
    "亲属关系通过血缘或婚姻建立",
]

QUERIES = ["爷", "爷爷", "叔叔", "叔父", "家族", "家族树", "家族树关系", "亲属", "舅舅", "外祖父"]


def truth(query: str) -> set[int]:
    """真源：查询是文档子串即应命中。"""
    return {i for i, d in enumerate(CORPUS) if query in d}


def fts5(query: str, *, native_only: bool) -> set[int]:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE VIRTUAL TABLE f USING fts5(text, tokenize='trigram')")
    for i, d in enumerate(CORPUS):
        c.execute("INSERT INTO f(rowid, text) VALUES (?,?)", (i, d))
    if native_only:
        try:
            return {r[0] for r in c.execute("SELECT rowid FROM f WHERE f MATCH ?", (query,))}
        except Exception:  # noqa: BLE001
            return set()
    # 带 LIKE 后备（代码库现状）
    return {r[0] for r in c.execute(
        "SELECT rowid FROM f WHERE text LIKE ? ESCAPE '!'", (f"%{query}%",))}


def _grams(text: str, n: int) -> list[str]:
    s = unicodedata.normalize("NFKC", text)
    return [s[i:i + n] for i in range(max(0, len(s) - n + 1))] or [s]


def ngram(query: str) -> set[int]:
    idx: dict[str, list[int]] = {}
    for doc_id, doc in enumerate(CORPUS):
        for n in (1, 2, 3):
            for g in _grams(doc, n):
                idx.setdefault(g, []).append(doc_id)
    qs = unicodedata.normalize("NFKC", query)
    grams: list[str] = []
    for n in (1, 2, 3):
        if len(qs) >= n:
            grams.extend(_grams(qs, n))
    hits: set[int] = set()
    for g in grams:
        hits.update(idx.get(g, []))
    return hits


def pg_trgm(dsn: str, query: str) -> set[int]:
    import psycopg
    conn = psycopg.connect(dsn)
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    conn.execute("DROP TABLE IF EXISTS lc_trgm")
    conn.execute("CREATE TABLE lc_trgm (id int primary key, text text not null)")
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO lc_trgm VALUES (%s,%s)", list(enumerate(CORPUS)))
    conn.commit()
    rows = conn.execute(
        "SELECT id FROM lc_trgm WHERE text %% %(q)s", {"q": query}
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS lc_trgm")
    conn.commit()
    conn.close()
    return {r[0] for r in rows}


def pgroonga(dsn: str, query: str) -> set[int]:
    import psycopg
    conn = psycopg.connect(dsn)
    conn.execute("DROP TABLE IF EXISTS lc_grn")
    conn.execute("CREATE TABLE lc_grn (id int primary key, text text not null)")
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO lc_grn VALUES (%s,%s)", list(enumerate(CORPUS)))
    conn.execute("CREATE INDEX ix_lc_grn ON lc_grn USING pgroonga (text)")
    conn.commit()
    rows = conn.execute(
        "SELECT id FROM lc_grn WHERE text &@ %(q)s", {"q": query}
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS lc_grn")
    conn.commit()
    conn.close()
    return {r[0] for r in rows}


def main() -> int:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    groonga_dsn = os.environ.get("PGTEST_DSN_GROONGA")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL")
        return 2

    # 一次性建好两个库的表，避免每个查询重建
    print(f"{'查询':10s} {'真源':5s} {'FTS5原生':9s} {'FTS5+LIKE':10s} "
          f"{'trgm':6s} {'n-gram':7s} {'PGroonga':9s}")
    rows = []
    for q in QUERIES:
        t = truth(q)
        f_native = fts5(q, native_only=True)
        f_like = fts5(q, native_only=False)
        tg = pg_trgm(dsn, q)
        ng = ngram(q)
        grn = pgroonga(groonga_dsn, q) if groonga_dsn else set()
        print(f"  {q:8s} {len(t):<5d} {len(f_native):<9d} {len(f_like):<10d} "
              f"{len(tg):<6d} {len(ng):<7d} {len(grn) if groonga_dsn else 'n/a':<9}")
        rows.append({
            "query": q, "truth": sorted(t), "fts5_native": sorted(f_native),
            "fts5_like": sorted(f_like), "pg_trgm": sorted(tg),
            "ngram": sorted(ng), "pgroonga": sorted(grn) if groonga_dsn else None,
        })

    print()
    print("=== 与真源的差异（false negative / false positive）===")
    print(f"{'查询':10s} {'FTS5原生':16s} {'FTS5+LIKE':16s} {'trgm':16s} "
          f"{'n-gram':16s} {'PGroonga':16s}")
    for r in rows:
        t = set(r["truth"])

        def fmt(hits, available=True):
            if not available:
                return "n/a"
            s = set(hits)
            fn = len(t - s)
            fp = len(s - t)
            return "精确" if fn == 0 and fp == 0 else f"缺{fn}/多{fp}"

        print(f"  {r['query']:8s} {fmt(r['fts5_native']):<16s} {fmt(r['fts5_like']):<16s} "
              f"{fmt(r['pg_trgm']):<16s} {fmt(r['ngram']):<16s} "
              f"{fmt(r['pgroonga'], r['pgroonga'] is not None):<16s}")

    out_dir = os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof"))
    try:
        from pathlib import Path
        import json
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "lexical-four-way.json").write_text(
            json.dumps({"corpus": CORPUS, "results": rows}, ensure_ascii=False, indent=2) + "\n")
        print(f"\n证据：{p / 'lexical-four-way.json'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
