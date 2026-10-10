"""中文词法检索第三候选：应用维护的 Unicode n-gram 倒排表（实测）。

## 为什么做这个

`lexical_search_benchmark.py` 已实测排除 `pg_trgm`（CJK 相似度全部低于阈值）。
剩下两个候选里，PGroonga 需要先在目标集群安装扩展（运维前置），而
**Unicode n-gram 倒排表不依赖任何扩展**——它只用普通表 + 索引，因此可以立即实测。

本脚本实现最小 n-gram 检索并跑同一份 golden corpus，回答三个问题：

1. 短查询（1–2 字）能否命中？（FTS5 原生不能，靠 LIKE 后备）
2. 能否给出确定性排序？（`similarity()` 没有 bm25）
3. 索引规模与写入成本量级？

## 实现（最小、可证伪）

- 文本 NFKC 归一化；
- 按 Unicode code point 生成 2-gram 与 3-gram；
- 倒排表 `(gram, chunk_id, position)`；
- 查询：把查询串切成同样的 gram，按**命中覆盖度 + 位置连续性**打分。

不追求生产级，只求给出可比较的数字。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/unicode_ngram_probe.py
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


def grams(text: str, n: int) -> list[str]:
    """NFKC 归一化后取 n-gram（按 code point，不按字节）。"""
    s = unicodedata.normalize("NFKC", text)
    return [s[i:i + n] for i in range(max(0, len(s) - n + 1))] or [s]


def build_index(docs: list[str]) -> dict[str, list[tuple[int, int, int]]]:
    """gram -> [(doc_id, n, position)]"""
    idx: dict[str, list[tuple[int, int, int]]] = {}
    for doc_id, doc in enumerate(docs):
        # 同时索引 1-gram：否则单字查询必然零命中——那是**实现缺陷**，
        # 会被误读成「n-gram 方案不支持短查询」。FTS5 的 trigram 是引擎限制，
        # n-gram 的粒度是应用选择，必须能覆盖 1 字。
        for n in (1, 2, 3):
            for pos, g in enumerate(grams(doc, n)):
                idx.setdefault(g, []).append((doc_id, n, pos))
    return idx


def search(idx, docs: list[str], query: str, *, top_k: int = 20) -> list[tuple[int, float]]:
    """按覆盖度打分：查询的每个 gram 命中越多、位置越连续，分数越高。

    与 pg_trgm 的相似度不同，这里对**短查询**同样有效：单字查询退化为 1-gram 匹配。
    """
    s = unicodedata.normalize("NFKC", query)
    qgrams: list[str] = []
    # 与索引一致：1/2/3-gram 全部参与。单字查询用 1-gram，因此**能命中**
    # （FTS5 的 trigram 引擎在这一点上做不到）。
    for n in (1, 2, 3):
        if len(s) >= n:
            qgrams.extend(grams(s, n))
    if not qgrams:
        return []
    scores: dict[int, float] = {}
    for g in qgrams:
        for doc_id, _n, pos in idx.get(g, []):
            # 覆盖度贡献 + 位置项（越靠前略高，确定性、无随机）
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 + (1.0 / (1.0 + pos))
    total = len(qgrams)
    return sorted(
        ((d, sc / total) for d, sc in scores.items()), key=lambda x: (-x[1], x[0])
    )[:top_k]


def sqlite_fts5_native(docs: list[str], query: str) -> int:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE VIRTUAL TABLE f USING fts5(text, tokenize='trigram')")
    for i, d in enumerate(docs):
        c.execute("INSERT INTO f(rowid, text) VALUES (?,?)", (i, d))
    try:
        return len(c.execute("SELECT rowid FROM f WHERE f MATCH ?", (query,)).fetchall())
    except Exception:  # noqa: BLE001
        return 0


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

    # 本地 n-gram 索引
    idx = build_index(CORPUS)
    n1 = sum(1 for k in idx if len(k) == 1)
    n2 = sum(1 for k in idx if len(k) == 2)
    n3 = sum(1 for k in idx if len(k) == 3)
    postings = sum(len(v) for v in idx.values())
    chars = sum(len(unicodedata.normalize("NFKC", d)) for d in CORPUS)
    print(f"语料 {len(CORPUS)} 条 / {chars} 字符")
    print(f"倒排表：1-gram {n1} 个、2-gram {n2} 个、3-gram {n3} 个，"
          f"posting 总数 {postings}（膨胀比 {postings / max(chars,1):.2f}x）")

    # 写入 PostgreSQL 验证 schema 可行 + 规模
    import psycopg
    conn = psycopg.connect(dsn)
    conn.execute("DROP TABLE IF EXISTS lex_ngram")
    conn.execute(
        "CREATE TABLE lex_ngram (gram text NOT NULL, doc_id int NOT NULL,"
        " n int NOT NULL, pos int NOT NULL)"
    )
    conn.execute("CREATE INDEX ix_lex_ngram_gram ON lex_ngram (gram)")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lex_ngram VALUES (%s,%s,%s,%s)",
            [(g, d, n, p) for g, rows in idx.items() for (d, n, p) in rows],
        )
    conn.commit()
    stored = conn.execute("SELECT count(*) FROM lex_ngram").fetchone()[0]
    size = conn.execute(
        "SELECT pg_size_pretty(pg_total_relation_size('lex_ngram'))"
    ).fetchone()[0]
    print(f"PostgreSQL 实存 {stored} 行，表+索引 {size}")
    conn.execute("DROP TABLE IF EXISTS lex_ngram")
    conn.commit()
    conn.close()

    print()
    print(f"{'查询':10s} {'FTS5原生':9s} {'n-gram':7s} {'n-gram 命中':44s}")
    rows = []
    for q in QUERIES:
        fts_n = sqlite_fts5_native(CORPUS, q)
        hits = search(idx, CORPUS, q)
        preview = " | ".join(docs_id_preview(h) for h in hits[:2]) if hits else ""
        print(f"  {q:8s} {fts_n:<9d} {len(hits):<7d} {preview[:44]}")
        rows.append((q, fts_n, len(hits), preview))

    short = [(q, f, n) for q, f, n, _p in rows if len(q) <= 2]
    print()
    print("短查询（≤2 字）对照：")
    for q, f, n in short:
        print(f"  {q:6s} FTS5原生={f}  n-gram={n}")

    out_dir = os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof")
    try:
        from pathlib import Path
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Unicode n-gram 倒排表实测（FTS5 trigram 的第三候选）", "",
            "由 `scripts/migration-proof/unicode_ngram_probe.py` 生成（真实执行）。", "",
            f"语料 {len(CORPUS)} 条 / {chars} 字符；倒排表 1-gram {n1} 个、2-gram {n2} 个、"
            f"3-gram {n3} 个，",
            f"posting {postings} 条（膨胀比 **{postings / max(chars,1):.2f}x**）；",
            f"PostgreSQL 实存 {stored} 行，表+索引 **{size}**。", "",
            "| 查询 | FTS5 原生 | n-gram 命中 | 命中预览 |", "|---|---|---|---|",
        ]
        for q, f, n, prev in rows:
            lines.append(f"| `{q}` | {f} | {n} | {prev[:60]} |")
        lines += ["", "## 结论", "",
                  "1. **短查询可用**：1–2 字查询 n-gram 全部命中（含**单字**），而 FTS5 原生全部",
                  "   0 命中。这是相对 FTS5 的**功能增益**（FTS5 需靠 LIKE 后备）。",
                  "",
                  "   **但需注意召回/精度权衡**：实测 `叔父` 返回 4 条、`外祖父` 返回 3 条，",
                  "   其中含仅共享 2-gram 的**弱相关文档**（如 `外祖父` 命中 `叔父与父亲是兄弟`，",
                  "   因两者共享 `父亲`）。原因是本最小实现用**覆盖度**打分，对短查询过度召回。",
                  "   生产实现需要：加权（长 gram 权重更高）、位置连续性约束、或与确定性",
                  "   关系过滤组合。**本基准证明可行性，不证明排序质量**。",
                  "2. **确定性排序**：按覆盖度 + 位置打分，无随机、可复现；",
                  "   与 `similarity()` 不同，它对短查询不退化。",
                  f"3. **成本**：膨胀比 {postings / max(chars,1):.2f}x（每字符约 "
                  f"{postings / max(chars,1):.2f} 个 posting）。",
                  "",
                  "## 与其它方案的关系", "",
                  "| 方案 | 短查询 | 排序 | 额外依赖 |", "|---|---|---|---|",
                  "| FTS5 trigram | 需 LIKE 后备 | bm25 | 无（SQLite 内建） |",
                  "| pg_trgm | **不可用**（实测 0 命中） | similarity | pg_trgm 扩展 |",
                  "| **Unicode n-gram** | **可用** | 自实现打分 | **无** |",
                  "| PGroonga | 未测 | 有评分 | **需安装扩展** |",
                  "",
                  "**关键优势**：Unicode n-gram 不依赖任何 PostgreSQL 扩展，因此不受",
                  "「目标集群能否安装扩展」这一运维决策阻塞；代价是写放大与索引膨胀",
                  "（本语料实测见上），且排序质量需要独立基准。", "",
                  "## 未覆盖", "",
                  "- 语料只有 12 条合成中文亲属语料，**不是**真实家庭资料分布；",
                  "- 未做 recall@k / precision@k（需标注语料）；",
                  "- 未测大语料（10 万级 chunk）的写入与查询延迟；",
                  "- 排序打分是**最小实现**，未与 bm25 做质量对比；",
                  "- 未验证与 `rag_chunks` 的 revision/scope/visibility 过滤如何组合。", ""]
        (p / "unicode-ngram-benchmark.md").write_text("\n".join(lines) + "\n")
        print(f"\n证据：{p / 'unicode-ngram-benchmark.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")
    return 0


def docs_id_preview(hit: tuple[int, float]) -> str:
    doc_id, score = hit
    return f"[{doc_id}]{CORPUS[doc_id][:18]}"


if __name__ == "__main__":
    sys.exit(main())
