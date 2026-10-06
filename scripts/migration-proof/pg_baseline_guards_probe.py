"""Gate 3 剩余项：refusal guard、重复执行幂等、中断恢复、审计 exactly-once。

补 `pg_baseline_prototype.py` 未覆盖的四类门禁：

| 项 | 验证方式 |
|---|---|
| refusal guard | 制造「前置条件不满足」的数据，断言迁移**在任何 DDL 前**拒绝 |
| 重复执行幂等 | 同一迁移跑两次，断言第二次不改变状态、不报错 |
| 中断恢复 | 事务中途回滚，断言无半迁移状态 |
| 审计 exactly-once | 每个真实操作恰好一条审计，重复尝试不产生第二条 |

`refusal guard` 的顺序是硬约束（memory #398）：**必须在任何 DDL 或 Alembic 版本移动之前**。
本探针用「先查后改」的实现形态并断言顺序。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/pg_baseline_guards_probe.py
"""
from __future__ import annotations

import os
import sys

SETUP = """
DROP TABLE IF EXISTS bg_rows, bg_audit, bg_version CASCADE;
CREATE TABLE bg_rows (id int PRIMARY KEY, legacy_role text, migrated boolean NOT NULL DEFAULT false);
CREATE TABLE bg_audit (id serial PRIMARY KEY, action text NOT NULL, row_id int);
CREATE TABLE bg_version (id int PRIMARY KEY, version int NOT NULL);
INSERT INTO bg_version VALUES (1, 1);
"""


def _conn(dsn: str):
    import psycopg
    return psycopg.connect(dsn)


def migrate(dsn: str, *, fail_midway: bool = False) -> str:
    """模拟一次带 refusal guard 的迁移。

    顺序（硬约束）：
    1. **先**跑 refusal guard（不满足即抛错，未做任何 DDL/版本移动）；
    2. 再 DDL / 数据改写；
    3. 最后移动版本号；
    4. 每个真实改写写恰好一条审计。
    """
    import psycopg
    conn = _conn(dsn)
    try:
        with conn.transaction():
            # ---- 1. refusal guard：必须在任何变更之前 ----
            unknown = conn.execute(
                "SELECT count(*) FROM bg_rows WHERE legacy_role IS NOT NULL"
                " AND legacy_role NOT IN ('space_admin','member')"
            ).fetchone()[0]
            if unknown:
                raise RuntimeError(
                    f"refusal: {unknown} 行的 legacy_role 不在新枚举内，需显式数据决策"
                )

            # ---- 2. DDL / 数据改写 ----
            #
            # 幂等的关键：只对**本次真正改动**的行写审计。
            # 初版写成「UPDATE ... WHERE migrated=false」再「SELECT ... WHERE migrated=true」，
            # 第二次执行会把**上次已迁移**的行也算进来，审计翻倍——探针据此报 FAIL。
            # 正确做法是用 RETURNING 取回本次改动的行集合。
            changed = conn.execute(
                "UPDATE bg_rows SET migrated = true WHERE migrated = false RETURNING id"
            ).fetchall()
            for (rid,) in changed:
                conn.execute("INSERT INTO bg_audit (action, row_id) VALUES ('migrate', %s)", (rid,))

            if fail_midway:
                raise RuntimeError("injected mid-migration failure")

            # ---- 3. 版本移动（最后）----
            conn.execute("UPDATE bg_version SET version = 2 WHERE id = 1")
        return "ok"
    except RuntimeError as exc:
        return f"refused:{exc}"
    finally:
        conn.close()


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

    failures: list[str] = []

    # ---------- 1. refusal guard：非法数据必须在 DDL/版本移动前被拒 ----------
    with _conn(dsn) as c:
        c.execute(SETUP)
        c.execute("INSERT INTO bg_rows VALUES (1,'space_admin',false),(2,'guest',false)")
        c.commit()
    result = migrate(dsn)
    with _conn(dsn) as c:
        ver = c.execute("SELECT version FROM bg_version WHERE id=1").fetchone()[0]
        migrated = c.execute("SELECT count(*) FROM bg_rows WHERE migrated").fetchone()[0]
        audits = c.execute("SELECT count(*) FROM bg_audit").fetchone()[0]
    ok = (result.startswith("refused") and ver == 1 and migrated == 0 and audits == 0)
    print(f"  [{'OK ' if ok else 'BAD'}] refusal guard -> {result[:60]}")
    print(f"        版本={ver}（应 1）迁移行={migrated}（应 0）审计={audits}（应 0）")
    if not ok:
        failures.append("refusal guard 未在任何变更前阻止迁移")

    # ---------- 2. 中断恢复：中途失败不得留下半迁移状态 ----------
    with _conn(dsn) as c:
        c.execute("DELETE FROM bg_rows; DELETE FROM bg_audit;")
        c.execute("INSERT INTO bg_rows VALUES (1,'space_admin',false),(2,'member',false)")
        c.commit()
    result = migrate(dsn, fail_midway=True)
    with _conn(dsn) as c:
        ver = c.execute("SELECT version FROM bg_version WHERE id=1").fetchone()[0]
        migrated = c.execute("SELECT count(*) FROM bg_rows WHERE migrated").fetchone()[0]
        audits = c.execute("SELECT count(*) FROM bg_audit").fetchone()[0]
    ok = (result.startswith("refused") and ver == 1 and migrated == 0 and audits == 0)
    print(f"  [{'OK ' if ok else 'BAD'}] 中断恢复 -> 版本={ver} 迁移行={migrated} 审计={audits}"
          "（应 1/0/0，全部回滚）")
    if not ok:
        failures.append("中途失败留下了半迁移状态")

    # ---------- 3. 重复执行幂等 ----------
    with _conn(dsn) as c:
        c.execute("DELETE FROM bg_rows; DELETE FROM bg_audit;")
        c.execute("INSERT INTO bg_rows VALUES (1,'space_admin',false),(2,'member',false)")
        c.commit()
    first = migrate(dsn)
    with _conn(dsn) as c:
        v1 = c.execute("SELECT version FROM bg_version WHERE id=1").fetchone()[0]
        a1 = c.execute("SELECT count(*) FROM bg_audit").fetchone()[0]
    second = migrate(dsn)
    with _conn(dsn) as c:
        v2 = c.execute("SELECT version FROM bg_version WHERE id=1").fetchone()[0]
        a2 = c.execute("SELECT count(*) FROM bg_audit").fetchone()[0]
    ok = (first == "ok" and second == "ok" and v1 == v2 == 2 and a1 == a2)
    print(f"  [{'OK ' if ok else 'BAD'}] 重复执行 -> 第一次={first} 第二次={second} "
          f"版本 {v1}->{v2} 审计 {a1}->{a2}（应 2/2 且审计不增）")
    if not ok:
        failures.append("重复执行不幂等（审计重复或版本漂移）")

    # ---------- 4. 审计 exactly-once ----------
    with _conn(dsn) as c:
        rows = c.execute("SELECT count(*) FROM bg_rows").fetchone()[0]
        per_row = c.execute(
            "SELECT row_id, count(*) FROM bg_audit GROUP BY row_id ORDER BY row_id"
        ).fetchall()
    ok = len(per_row) == rows and all(n == 1 for _rid, n in per_row)
    print(f"  [{'OK ' if ok else 'BAD'}] 审计 exactly-once -> {per_row}（每行恰好 1 条）")
    if not ok:
        failures.append(f"审计不是 exactly-once：{per_row}")

    with _conn(dsn) as c:
        c.execute("DROP TABLE IF EXISTS bg_rows, bg_audit, bg_version CASCADE")
        c.commit()

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: refusal 顺序、中断回滚、重复幂等、审计 exactly-once 均符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
