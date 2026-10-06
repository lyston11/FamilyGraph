"""Gate 5：静态快照 → 导入 → 拒绝式对账（隔离环境，可复跑）。

## 设计约束（来自 design.md §7）

- 只用**静态 SQLite 快照**（`app.backup` 的 online backup API 产物），
  不复制运行中的主库文件；
- 保留 ID / 时间 / 状态 / revision / FK；
- 对账行数、摘要、scope、状态、run↔attempt；
- 对账失败即 **refusal**：不自动修数据、不切 writer；
- 重复导入必须幂等。

本脚本用**合成** SQLite 库验证机制，不触碰开发库或线上库。

用法：

    PGTEST_DSN=postgresql://... python3 research/tools/import_reconcile_probe.py
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

SCHEMA = """
CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE spaces (id INTEGER PRIMARY KEY, owner_id INTEGER REFERENCES accounts(id), name TEXT);
CREATE TABLE agent_runs (
  id INTEGER PRIMARY KEY, space_id INTEGER, account_id INTEGER, status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE steward_model_calls (
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES agent_runs(id), space_id INTEGER,
  status TEXT NOT NULL, applied_at TEXT, billed_tokens INTEGER NOT NULL DEFAULT 0
);
"""


def build_snapshot(path: Path) -> None:
    """用 online backup API 生成静态快照（与 app.backup 同一机制）。"""
    live = sqlite3.connect(":memory:")
    live.executescript(SCHEMA)
    live.execute("INSERT INTO accounts VALUES (1,'a'),(2,'b')")
    live.execute("INSERT INTO spaces VALUES (1,1,'s1'),(2,2,'s2')")
    for i in range(1, 6):
        live.execute("INSERT INTO agent_runs VALUES (?,?,?,'succeeded',1,?)",
                     (i, (i % 2) + 1, 1, f"2026-10-0{i}T00:00:00Z"))
    for i in range(1, 6):
        live.execute("INSERT INTO steward_model_calls VALUES (?,?,?,'succeeded',?,100)",
                     (i, i, (i % 2) + 1, f"2026-10-0{i}T00:01:00Z"))
    live.commit()
    dst = sqlite3.connect(str(path))
    live.backup(dst)
    dst.close()
    live.close()


def row_digest(conn: sqlite3.Connection, table: str) -> str:
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(r).encode())
    return h.hexdigest()


def reconcile(src: sqlite3.Connection, dst) -> list[str]:
    """返回差异列表；空列表 = 对账通过。"""
    diffs: list[str] = []
    for table in ("accounts", "spaces", "agent_runs", "steward_model_calls"):
        n_src = src.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
        n_dst = dst.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if n_src != n_dst:
            diffs.append(f"{table}: 行数 {n_src} != {n_dst}")
            continue
        if row_digest(src, table) != row_digest_pg(dst, table):
            diffs.append(f"{table}: 摘要不一致")
    # 交叉不变量：每个 attempt 必须指向存在的 run
    orphan = dst.execute(
        "SELECT count(*) FROM steward_model_calls c LEFT JOIN agent_runs r ON r.id = c.run_id"
        " WHERE r.id IS NULL"
    ).fetchone()[0]
    if orphan:
        diffs.append(f"孤儿 attempt: {orphan}")
    # 状态分布
    for table, col in (("agent_runs", "status"), ("steward_model_calls", "status")):
        s = dict(src.execute(f"SELECT {col}, count(*) FROM {table} GROUP BY {col}").fetchall())  # noqa: S608
        d = dict(dst.execute(f"SELECT {col}, count(*) FROM {table} GROUP BY {col}").fetchall())
        if s != d:
            diffs.append(f"{table}.{col} 分布不一致: {s} != {d}")
    return diffs


def row_digest_pg(conn, table: str) -> str:
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(tuple(r)).encode())
    return h.hexdigest()


def import_snapshot(src: sqlite3.Connection, dst) -> None:
    dst.execute("DROP TABLE IF EXISTS steward_model_calls, agent_runs, spaces, accounts CASCADE")
    dst.execute("""
        CREATE TABLE accounts (id int PRIMARY KEY, name text NOT NULL);
        CREATE TABLE spaces (id int PRIMARY KEY, owner_id int REFERENCES accounts(id), name text);
        CREATE TABLE agent_runs (id int PRIMARY KEY, space_id int, account_id int,
                                 status text NOT NULL, attempt int NOT NULL DEFAULT 0,
                                 created_at text NOT NULL);
        CREATE TABLE steward_model_calls (id int PRIMARY KEY, run_id int REFERENCES agent_runs(id),
                                          space_id int, status text NOT NULL, applied_at text,
                                          billed_tokens int NOT NULL DEFAULT 0);
    """)
    for table in ("accounts", "spaces", "agent_runs", "steward_model_calls"):
        rows = src.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608
        if not rows:
            continue
        placeholders = ",".join(["%s"] * len(rows[0]))
        with dst.cursor() as cur:
            cur.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)  # noqa: S608
    # sequence 修复：PG 主键是 plain int（非 serial），真实迁移需 setval；此处显式说明
    dst.commit()


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

    # 安全守卫：本探针会 DROP 与业务同名的表。若目标库里已经存在**真实**业务表，
    # 立即拒绝运行，避免 PGTEST_DSN 误指开发库/线上时造成破坏。
    with psycopg.connect(dsn) as guard:
        existing = guard.execute(
            "SELECT count(*) FROM information_schema.tables"
            " WHERE table_schema='public' AND table_name IN"
            " ('agent_runs','steward_model_calls','accounts','spaces')"
        ).fetchone()[0]
    if existing:
        print(f"REFUSE: 目标库已存在 {existing} 张同名业务表，拒绝运行（PGTEST_DSN 必须指向空库）")
        return 3

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="fg-import-probe-") as tmp:
        snap = Path(tmp) / "snapshot.db"
        build_snapshot(snap)
        src = sqlite3.connect(str(snap))
        integrity = src.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"  快照 integrity_check = {integrity}")
        if integrity != "ok":
            failures.append("快照 integrity_check 失败")

        with psycopg.connect(dsn) as dst:
            import_snapshot(src, dst)
            diffs = reconcile(src, dst)
            print(f"  [{'OK ' if not diffs else 'BAD'}] 导入后对账 -> {diffs or '无差异'}")
            if diffs:
                failures.append(f"导入对账失败: {diffs}")

            # 幂等：重复导入必须收敛到同一结果
            import_snapshot(src, dst)
            diffs2 = reconcile(src, dst)
            print(f"  [{'OK ' if not diffs2 else 'BAD'}] 重复导入对账 -> {diffs2 or '无差异'}")
            if diffs2:
                failures.append(f"重复导入不幂等: {diffs2}")

            # 拒绝式：人为制造差异，对账必须报告且不自动修复
            dst.execute("UPDATE agent_runs SET status='failed' WHERE id=1")
            dst.commit()
            diffs3 = reconcile(src, dst)
            ok = bool(diffs3)
            print(f"  [{'OK ' if ok else 'BAD'}] 制造差异后被检出 -> {diffs3}")
            if not ok:
                failures.append("对账未检出人为差异（不能作为 refusal 门）")
            # refusal：不自动修复
            row = dst.execute("SELECT status FROM agent_runs WHERE id=1").fetchone()[0]
            if row != "failed":
                failures.append("对账意外自动修复了差异（必须 refusal）")
            else:
                print("  OK  对账未自动修复差异（refusal 语义保持）")

            dst.execute("DROP TABLE IF EXISTS steward_model_calls, agent_runs, spaces, accounts CASCADE")
            dst.commit()
        src.close()

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 快照/导入/对账/拒绝/幂等 均符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
