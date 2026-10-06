"""可复现的 PostgreSQL 方言阻塞探针（把 L2 从断言变成产物）。

用法（隔离 PostgreSQL，禁止指向开发库或线上）：

    PGTEST_DSN=postgresql://postgres:probe@127.0.0.1:55435/familygraph \\
        ./backend/.venv/bin/python research/tools/pg_replay_probe.py

覆盖三处历史迁移的 SQLite 专属构造，以及修复后的方言感知反证：

| 迁移 | 构造 | 预期 |
|---|---|---|
| 0042 | `json_extract` 写入 CHECK | `UndefinedFunction` |
| 0022 | `SELECT last_insert_rowid()` | `UndefinedFunction` |
| 0014 | `CREATE VIRTUAL TABLE ... USING fts5` | `SyntaxError` |
| 修复后 | `(col::jsonb -> 'k') = '1'::jsonb` | 成功 |

退出码 0 表示**全部符合预期**（阻塞确实发生、反证确实成功）。
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        import psycopg
    except ImportError:
        print("SKIP: psycopg 未安装")
        return 2
    dsn = os.environ.get("PGTEST_DSN")
    if not dsn:
        print("SKIP: 需要 PGTEST_DSN 指向隔离 PostgreSQL（不得指向开发库/线上）")
        return 2

    conn = psycopg.connect(dsn, connect_timeout=8)
    conn.execute("DROP TRIGGER IF EXISTS trg_agent_sessions_scope_immutable ON probe_mig")
    conn.execute("DROP FUNCTION IF EXISTS probe_guard()")
    conn.execute("DROP TABLE IF EXISTS probe_mig, probe_fts")
    conn.execute(
        "CREATE TABLE probe_mig (id serial primary key, source_kind text,"
        " source_verification text, source_type text, source_id text,"
        " source_revision int, source_span_json json)"
    )
    conn.commit()

    # (标签, SQL, 期望：blocked=True 表示**应当**失败)
    cases = [
        ("0042 json_extract CHECK", """ALTER TABLE probe_mig ADD CONSTRAINT ck_probe CHECK (
             source_verification = 'unverified' OR (source_kind != 'legacy'
             AND source_type IS NOT NULL AND source_id IS NOT NULL
             AND source_revision IS NOT NULL AND source_revision > 0
             AND coalesce(json_extract(source_span_json, '$.version') = 1, 0)
             AND coalesce(json_extract(source_span_json, '$.kind') = source_kind, 0)))""", True),
        ("0022 last_insert_rowid", "SELECT last_insert_rowid()", True),
        ("0014 FTS5 virtual table",
         "CREATE VIRTUAL TABLE probe_fts USING fts5(chunk_id UNINDEXED, text,"
         " tokenize='trigram')", True),
        ("fixed dialect CHECK",
         "ALTER TABLE probe_mig ADD CONSTRAINT ck_fixed CHECK ("
         "coalesce((source_span_json::jsonb -> 'version') = '1'::jsonb, false))", False),
        # ---- 触发器：14 个迁移触发器全部使用 SQLite 语法 ----
        ("0009 scope-immutable trigger (SQLite RAISE/ABORT)",
         """CREATE TRIGGER trg_agent_sessions_scope_immutable
            BEFORE UPDATE ON probe_mig
            WHEN OLD.id <> NEW.id
            BEGIN
                SELECT RAISE(ABORT, 'agent_sessions scope is immutable');
            END;""", True),
        ("0045 revision-counter trigger (SQLite upsert in body)",
         """CREATE TRIGGER sri_probe AFTER UPDATE ON probe_mig
            BEGIN
              INSERT INTO probe_mig (id) VALUES (1) ON CONFLICT(id) DO UPDATE SET id = 1;
            END;""", True),
        ("PostgreSQL form: function + trigger (control)",
         """CREATE FUNCTION probe_guard() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'immutable'; END; $$ LANGUAGE plpgsql""", False),
    ]

    failures = []
    for label, sql, expect_blocked in cases:
        try:
            conn.execute(sql)
            conn.commit()
            blocked = False
            detail = "succeeded"
        except Exception as exc:  # noqa: BLE001 - 探针要记录任意方言错误
            conn.rollback()
            blocked = True
            detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:90]}"
        ok = blocked == expect_blocked
        mark = "OK " if ok else "BAD"
        want = "应阻塞" if expect_blocked else "应成功"
        print(f"  [{mark}] {label}（{want}）-> {detail}")
        if not ok:
            failures.append(label)

    conn.execute("DROP TABLE IF EXISTS probe_mig, probe_fts")
    conn.commit()
    conn.close()

    if failures:
        print(f"FAIL: {failures}")
        return 1
    print("PASS: 全部阻塞与方言修复反证均符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
