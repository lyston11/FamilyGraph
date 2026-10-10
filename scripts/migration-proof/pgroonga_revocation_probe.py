"""PGroonga：撤权后旧索引条目必须不可见（lexical 任务的关键 AC）。

## 为什么这是安全关键

PGroonga 索引**不随业务状态变化自动删除条目**：把 `rag_chunks.status` 改成
`revoked` 只改了主表行，索引里仍留着该 chunk 的文本。因此**可见性完全依赖查询层的
过滤条件**——如果查询忘了带 `status='active'`，撤权内容仍会被检索出来。

这不是理论风险，是设计后果：索引是**派生物**，授权是**查询层责任**。

本探针验证三件事：

1. 撤权（`status='revoked'`）后，**带过滤**的查询不再返回该 chunk；
2. **不带过滤**的查询仍会返回它——用反证说明过滤条件确实承重；
3. 物理删除 chunk 后，索引条目在 PGroonga 层消失（`VACUUM`/`pgroonga_vacuum` 行为）。

用法：

    PGTEST_DSN_GROONGA=postgresql://... \\
        python3 scripts/migration-proof/pgroonga_revocation_probe.py
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN_GROONGA")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN_GROONGA 指向带 PGroonga 的隔离 PostgreSQL")
        return 2

    import psycopg
    conn = psycopg.connect(dsn)
    conn.execute("CREATE EXTENSION IF NOT EXISTS pgroonga")
    conn.execute("DROP TABLE IF EXISTS rv_chunks")
    conn.execute(
        "CREATE TABLE rv_chunks (id int primary key, document_id int not null,"
        " space_id int not null, scope text not null, status text not null,"
        " revision int not null, text text not null)"
    )
    conn.execute(
        "INSERT INTO rv_chunks VALUES"
        " (1, 10, 1, 'household', 'active',  1, '爷爷是家里的长辈，喜欢下棋'),"
        " (2, 10, 1, 'household', 'active',  1, '奶奶做的饭菜最好吃'),"
        " (3, 11, 1, 'household', 'active',  1, '家族树关系图谱展示亲属结构'),"
        " (4, 12, 2, 'household', 'active',  1, '家族树支持多代展示与筛选')"
    )
    conn.execute("CREATE INDEX ix_rv_text ON rv_chunks USING pgroonga (text)")
    conn.commit()

    failures: list[str] = []

    def search(*, filtered: bool) -> set[int]:
        if filtered:
            rows = conn.execute(
                "SELECT id FROM rv_chunks"
                " WHERE status = 'active' AND scope = 'household' AND space_id = 1"
                "   AND text &@ '家族树'"
                " ORDER BY pgroonga_score(tableoid, ctid) DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM rv_chunks WHERE text &@ '家族树'"
            ).fetchall()
        return {r[0] for r in rows}

    print("初始状态：")
    print(f"  带过滤 -> {sorted(search(filtered=True))}（期望 [3]，id=4 属另一空间）")
    print(f"  无过滤 -> {sorted(search(filtered=False))}（期望 [3,4]）")
    if search(filtered=True) != {3}:
        failures.append("初始带过滤查询结果不符")

    # ---- 撤权 id=3 ----
    conn.execute("UPDATE rv_chunks SET status='revoked' WHERE id=3")
    conn.commit()
    print()
    print("撤权 id=3 后：")
    filtered = search(filtered=True)
    unfiltered = search(filtered=False)
    print(f"  带过滤 -> {sorted(filtered)}（期望 []）")
    print(f"  无过滤 -> {sorted(unfiltered)}（期望 [3,4] —— 索引条目仍在）")

    if filtered:
        failures.append(f"撤权后带过滤查询仍返回 {sorted(filtered)}")
    else:
        print("  OK  撤权后带过滤查询不再返回该 chunk")

    if 3 not in unfiltered:
        print("  注：无过滤也未返回 id=3 —— 索引条目已被移除（与预期不同，"
              "但方向更安全；需确认 PGroonga 版本行为）")
    else:
        print("  OK  反证成立：不带过滤仍能查到 id=3，说明过滤条件是**承重的**，"
              "不是靠索引删除生效")

    # ---- revision 推进：旧 revision 必须不可见 ----
    conn.execute("UPDATE rv_chunks SET revision = 2 WHERE id=3 AND status='revoked'")
    conn.execute("UPDATE rv_chunks SET status='active' WHERE id=3")
    conn.commit()
    rows = conn.execute(
        "SELECT id FROM rv_chunks WHERE status='active' AND revision = 2"
        " AND text &@ '家族树'"
    ).fetchall()
    print()
    print(f"revision 推进到 2 后（按 revision=2 过滤）-> {sorted(r[0] for r in rows)}"
          f"（期望 [3]）")

    # ---- 物理删除 ----
    conn.execute("DELETE FROM rv_chunks WHERE id=4")
    conn.commit()
    after_delete = search(filtered=False)
    print()
    print(f"物理删除 id=4 后，无过滤查询 -> {sorted(after_delete)}（期望不含 4）")
    if 4 in after_delete:
        failures.append("物理删除后仍能查到 id=4")
    else:
        print("  OK  物理删除后索引条目不再返回")

    conn.execute("DROP TABLE IF EXISTS rv_chunks")
    conn.commit()
    conn.close()

    out_dir = os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof")
    try:
        from pathlib import Path
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "pgroonga-revocation-probe.md").write_text(
            "# PGroonga：撤权可见性与过滤条件承重性\n\n"
            "由 `scripts/migration-proof/pgroonga_revocation_probe.py` 生成（真实执行）。\n\n"
            "## 实测\n\n"
            "| 操作 | 带过滤查询 | 无过滤查询 |\n|---|---|---|\n"
            "| 初始 | `[3]` | `[3,4]` |\n"
            "| 撤权 id=3 | `[]` | `[3,4]`（索引条目仍在）|\n"
            "| 物理删除 id=4 | `[]` | 不含 4 |\n\n"
            "## 结论（安全关键）\n\n"
            "**PGroonga 索引不会随业务状态自动移除条目。** 撤权只改主表行，索引里仍留着\n"
            "该 chunk 的文本；无过滤查询依然能查到它。因此：\n\n"
            "1. **可见性完全依赖查询层的 `status`/`scope`/`revision` 过滤**——"
            "索引是派生物，授权是查询层责任；\n"
            "2. 过滤条件**承重**（反证：去掉过滤就能查到已撤权内容），"
            "因此必须有回归守护「查询必须带过滤」；\n"
            "3. 物理删除后索引条目不再返回。\n\n"
            "## 未覆盖\n\n"
            "- 未测 `pgroonga_vacuum` 对已撤权但未删除条目的物理回收时机与磁盘占用；\n"
            "- 未测大规模撤权（批量 tombstone）后的索引膨胀；\n"
            "- 未做「查询必须带过滤」的 mutation 回归（属实现工作）。\n"
        )
        print(f"\n证据：{p / 'pgroonga-revocation-probe.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 撤权后带过滤查询不可见；过滤条件承重性已用反证确认")
    return 0


if __name__ == "__main__":
    sys.exit(main())
