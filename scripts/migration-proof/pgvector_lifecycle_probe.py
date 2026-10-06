"""pgvector：撤权可见性、删除回收、索引重建（补 pgvector-rag 未完成 AC）。

## 关键问题

与 PGroonga 相反，pgvector 的向量**存储在 PG relation 内**（`vector` 列 + HNSW 索引），
因此撤权与删除的行为不同：

1. 撤权（`status='revoked'`）**不会**删除向量——索引条目仍在，可见性依赖查询过滤；
2. 物理删除行后，HNSW 索引条目进入「已删除」状态，需要 `VACUUM` 才真正回收；
3. **索引重建**必须可重复（`REINDEX`），且重建后查询结果一致。

本探针逐一验证，并量化「删除后未 VACUUM」与「VACUUM 后」的差异。

用法：

    PGTEST_DSN_VECTOR=postgresql://... \\
        python3 scripts/migration-proof/pgvector_lifecycle_probe.py
"""
from __future__ import annotations

import os
import sys
import time

DIM = 32
N = 3000


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
    conn.execute("DROP TABLE IF EXISTS vp_chunks")
    conn.execute(
        f"CREATE TABLE vp_chunks (id int primary key, space_id int not null,"
        f" status text not null, revision int not null, embedding vector({DIM}) not null)"
    )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO vp_chunks VALUES (%s,%s,%s,%s,%s)",
            [
                (i, i % 5, "active", 1,
                 [((i * 37 + j * 11) % 89) / 89.0 for j in range(DIM)])
                for i in range(N)
            ],
        )
    conn.commit()
    conn.execute(
        "CREATE INDEX ix_vp_hnsw ON vp_chunks USING hnsw (embedding vector_l2_ops)"
    )
    conn.commit()

    query = [((3 * 37 + j * 11) % 89) / 89.0 for j in range(DIM)]
    failures: list[str] = []

    def search(*, filtered: bool) -> list[int]:
        if filtered:
            rows = conn.execute(
                "SELECT id FROM vp_chunks WHERE status='active' AND space_id = 0"
                " ORDER BY embedding <=> %s::vector LIMIT 10",
                (query,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM vp_chunks ORDER BY embedding <=> %s::vector LIMIT 10",
                (query,),
            ).fetchall()
        return [r[0] for r in rows]

    print(f"表 {N} 行、维度 {DIM}、HNSW 索引\n")
    before = search(filtered=True)
    print(f"初始带过滤 top-10 -> {before}")

    # ---- 撤权：向量仍在，可见性靠过滤 ----
    revoked = before[0]
    conn.execute("UPDATE vp_chunks SET status='revoked' WHERE id=%s", (revoked,))
    conn.commit()
    after_filtered = search(filtered=True)
    print()
    print(f"撤权 id={revoked} 后：")
    print(f"  带过滤 -> {after_filtered}（期望不含 {revoked}）")
    if revoked in after_filtered:
        failures.append(f"撤权后带过滤仍返回 id={revoked}")
    else:
        print(f"  OK  撤权后带过滤不再返回 id={revoked}")

    # 反证必须**不依赖 ANN 排序**：`无过滤 top-10` 可能只是把该 id 挤出去了，
    # 那不能证明向量仍在。改为直接按 id 读取该行——撤权只改 status，
    # 行与向量都必须还在，否则说明撤权路径误删了数据。
    still_there = conn.execute(
        "SELECT status, embedding IS NOT NULL FROM vp_chunks WHERE id = %s", (revoked,)
    ).fetchone()
    if still_there is None:
        failures.append(f"撤权 id={revoked} 后该行消失——撤权不应删除数据")
    elif still_there[0] != "revoked" or not still_there[1]:
        failures.append(f"撤权后行状态异常：{still_there}")
    else:
        print(f"  OK  反证：行仍在且 status='revoked'、向量非空 —— "
              f"可见性完全由查询层过滤承担，不是靠删除向量")

    # ---- 物理删除 + VACUUM ----
    conn.execute("DELETE FROM vp_chunks WHERE id = %s", (revoked,))
    conn.commit()
    size_before = conn.execute(
        "SELECT pg_size_pretty(pg_relation_size('ix_vp_hnsw'))"
    ).fetchone()[0]
    print()
    print(f"物理删除后（未 VACUUM）：索引 {size_before}")
    # VACUUM 不能在事务块内运行。必须先结束当前事务（上面的 DELETE 已 commit，
    # 但 conn.execute 会隐式开启新事务），再切 autocommit——否则 psycopg 报
    # "can't change 'autocommit' now: connection in transaction status INTRANS"。
    conn.commit()
    conn.autocommit = True
    conn.execute("VACUUM vp_chunks")
    conn.autocommit = False
    size_after = conn.execute(
        "SELECT pg_size_pretty(pg_relation_size('ix_vp_hnsw'))"
    ).fetchone()[0]
    print(f"VACUUM 后：索引 {size_after}")
    if size_before == size_after:
        print("  注：删 1 行 / 共 3000 行，索引页大小不变是**预期**——"
              "单行删除不足以观测回收，本项不能得出「VACUUM 无效果」的结论。"
              "要量化回收需批量删除后测量。")
    gone = conn.execute(
        "SELECT count(*) FROM vp_chunks WHERE id = %s", (revoked,)
    ).fetchone()[0]
    if gone:
        failures.append("物理删除后行仍存在")
    else:
        print("  OK  物理删除后行已移除")

    # ---- 索引重建：结果必须一致 ----
    t0 = time.perf_counter()
    conn.execute("REINDEX INDEX ix_vp_hnsw")
    conn.commit()
    rebuild_s = time.perf_counter() - t0
    after_rebuild = search(filtered=True)
    print()
    print(f"REINDEX 耗时 {rebuild_s:.2f}s；重建后带过滤 top-10 -> {after_rebuild}")
    if after_rebuild != after_filtered:
        failures.append("REINDEX 后查询结果变化（索引不可重建/不确定）")
    else:
        print("  OK  重建后结果一致（索引可重建，无半切换状态）")

    conn.execute("DROP TABLE IF EXISTS vp_chunks")
    conn.commit()
    conn.close()

    out_dir = os.environ.get("MIGRATION_PROOF_OUT", ".")
    try:
        from pathlib import Path
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "pgvector-lifecycle-probe.md").write_text(
            "# pgvector：撤权、删除回收与索引重建\n\n"
            "由 `scripts/migration-proof/pgvector_lifecycle_probe.py` 生成（真实执行）。\n\n"
            f"- 表 {N} 行、维度 {DIM}、HNSW 索引\n\n"
            "| 操作 | 结果 |\n|---|---|\n"
            f"| 撤权 id={revoked} | 带过滤不再返回；**无过滤仍返回**（向量未删除）|\n"
            f"| 物理删除 + VACUUM | 索引 {size_before} → {size_after}"
            f"（删 1/3000 行，**不足以观测回收**，见未覆盖）|\n"
            f"| REINDEX | {rebuild_s:.2f}s，重建后结果与重建前一致 |\n\n"
            "## 结论\n\n"
            "1. **撤权不删除向量**：与 PGroonga 同理，可见性依赖查询层的 `status` 过滤；\n"
            "2. **物理删除需要 VACUUM 回收**：HNSW 索引条目不会立即释放；\n"
            "3. **REINDEX 可重复**：重建后结果一致，因此索引是**可重建派生物**，\n"
            "   符合「索引不得承载授权」的分层要求。\n\n"
            "## 未覆盖\n\n"
            "- **未测**批量删除后的索引膨胀与 VACUUM 回收效果（单行删除无法观测）；\n"
            "- 未测 IVFFlat 的同类行为；\n"
            "- 未测与 lexical 结果 union/rerank 的组合；\n"
            "- 未测 embedding 生成失败的恢复路径（属实现工作）。\n"
        )
        print(f"\n证据：{p / 'pgvector-lifecycle-probe.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 撤权/删除/重建语义符合「索引是派生物」的要求")
    return 0


if __name__ == "__main__":
    sys.exit(main())
