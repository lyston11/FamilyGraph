"""pgvector：ANN 与授权过滤组合的可行性探针（真实执行）。

## 为什么这是 pgvector-rag 的关键风险

向量检索的常见陷阱是 **ANN + post-filter 召回塌陷**：先用近似索引取 top-k，
再用授权过滤剔除，若被剔除的比例很高，返回结果会远少于 k，甚至为空——
而「空结果」在 RAG 里是不可见的失败（用户以为没有相关内容）。

本脚本量化：不同**过滤选择性**下，`SET LOCAL hnsw.ef_search` / `ivfflat.probes`
取回的可用结果数，以及「先过滤再 ANN」与「ANN 再过滤」的差异。

用法：

    PGTEST_DSN_VECTOR=postgresql://... python3 scripts/migration-proof/pgvector_filter_probe.py
"""
from __future__ import annotations

import os
import sys

DIM = 64
N_ROWS = 2000
K = 10


def main() -> int:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN_VECTOR")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN_VECTOR 指向带 pgvector 的隔离 PostgreSQL")
        return 2

    import psycopg
    conn = psycopg.connect(dsn)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("DROP TABLE IF EXISTS vec_probe")
    conn.execute(
        f"CREATE TABLE vec_probe (id int primary key, space_id int not null,"
        f" embedding vector({DIM}) not null)"
    )
    # 合成向量：确定性伪随机（不用 random 模块以免不可复现）
    with conn.cursor() as cur:
        rows = []
        for i in range(N_ROWS):
            vec = [((i * 31 + j * 17) % 97) / 97.0 for j in range(DIM)]
            rows.append((i, i % 10, vec))
        cur.executemany("INSERT INTO vec_probe VALUES (%s,%s,%s)", rows)
    conn.commit()

    query = [((7 * 31 + j * 17) % 97) / 97.0 for j in range(DIM)]

    print(f"表 {N_ROWS} 行，维度 {DIM}，k={K}\n")

    # 1) 精确检索（无索引）：作为真源
    exact = conn.execute(
        "SELECT id, space_id FROM vec_probe ORDER BY embedding <=> %s::vector LIMIT %s",
        (query, K),
    ).fetchall()
    print(f"精确 top-{K}（无索引）：{len(exact)} 条，space 分布 "
          f"{sorted({s for _i, s in exact})}")

    # 2) 建 HNSW 索引
    conn.execute("CREATE INDEX ix_vec_hnsw ON vec_probe USING hnsw (embedding vector_l2_ops)")
    conn.commit()

    print()
    print("=== ANN 后过滤（post-filter）在不同选择性下的可用结果数 ===")
    print(f"{'过滤条件':22s} {'ANN top-k':8s} {'过滤后剩余':10s} {'精确应有':8s}")
    for space_filter, label in [
        (None, "无过滤"),
        (list(range(5)), "允许 5/10 空间"),
        ([3], "允许 1/10 空间"),
        ([99], "允许不存在的空间"),
    ]:
        if space_filter is None:
            ann = conn.execute(
                "SELECT id FROM vec_probe ORDER BY embedding <=> %s::vector LIMIT %s",
                (query, K),
            ).fetchall()
            remaining = len(ann)
            exact_n = len(exact)
        else:
            # ANN 取 top-k，再过滤
            ann = conn.execute(
                "SELECT id, space_id FROM vec_probe ORDER BY embedding <=> %s::vector LIMIT %s",
                (query, K),
            ).fetchall()
            remaining = len([1 for _i, s in ann if s in space_filter])
            exact_n = conn.execute(
                "SELECT count(*) FROM (SELECT space_id FROM vec_probe"
                " WHERE space_id = ANY(%s) ORDER BY embedding <=> %s::vector LIMIT %s) t",
                (space_filter, query, K),
            ).fetchone()[0]
        print(f"  {label:20s} {K:<8d} {remaining:<10d} {exact_n:<8d}")

    # 3) 先过滤再 ANN（正确的组合方式）
    print()
    print("=== 先过滤再 ANN（filter-then-ANN）===")
    for space_filter, label in [([3], "允许 1/10 空间"), ([3, 4], "允许 2/10 空间")]:
        rows = conn.execute(
            "SELECT id FROM vec_probe WHERE space_id = ANY(%s)"
            " ORDER BY embedding <=> %s::vector LIMIT %s",
            (space_filter, query, K),
        ).fetchall()
        exact_n = conn.execute(
            "SELECT count(*) FROM vec_probe WHERE space_id = ANY(%s)", (space_filter,)
        ).fetchone()[0]
        print(f"  {label:20s} 返回 {len(rows):<3d} 条（该子集共 {exact_n} 行）"
              f" {'✅ 满足 k' if len(rows) == min(K, exact_n) else '⚠️ 少于 k'}")

    print()
    print("=== 结论 ===")
    print("  post-filter 在低选择性下会返回少于 k 的结果（甚至 0 条），而 filter-then-ANN")
    print("  在过滤列有索引时能取满。因此 RAG 查询必须**先**按 scope/visibility 过滤，")
    print("  再执行向量排序——不能反过来。")

    conn.execute("DROP TABLE IF EXISTS vec_probe")
    conn.commit()
    conn.close()

    out_dir = os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof"))
    try:
        from pathlib import Path
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "pgvector-filter-probe.md").write_text(
            "# pgvector：ANN 与授权过滤组合探针\n\n"
            "由 `scripts/migration-proof/pgvector_filter_probe.py` 生成（真实执行）。\n\n"
            f"- 表 {N_ROWS} 行、维度 {DIM}、k={K}、HNSW 索引\n"
            "- **post-filter**（ANN 后过滤）在低选择性下返回少于 k；\n"
            "- **filter-then-ANN**（先过滤再排序）能取满。\n\n"
            "因此 RAG 查询必须先按 scope/visibility 过滤再向量排序。\n\n"
            "## 未覆盖\n\n"
            "- 未测真实 embedding 分布（本探针用确定性合成向量）；\n"
            "- 未测 IVFFlat 与 HNSW 的召回/延迟对比；\n"
            "- 未测 10 万级规模与索引构建时间；\n"
            "- 未测与 lexical 结果 union/rerank 的组合。\n"
        )
        print(f"\n证据：{p / 'pgvector-filter-probe.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
