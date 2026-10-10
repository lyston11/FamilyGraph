"""Gate 1 缺口：方言语义矩阵（两库真实执行，语义比对而非字符串比对）。

覆盖 PG-1 要求的矩阵维度：NULL 排序/唯一性、JSON 类型、整数除法、布尔、字符串比较。

## 三个必须避开的陷阱（初版全踩了，记录以免重犯）

1. **不能用 SQLite 专属函数**：初版用 `group_concat`（SQLite）拼结果，PG 上直接
   `UndefinedFunction` —— 于是把「探针写错」误报成「方言差异」。每侧必须用各自
   惯用 SQL（`group_concat` vs `string_agg`）。
2. **不能比对 repr**：SQLite 返回 `0/1`，PG 返回 `False/True`，语义相同但字符串
   不同。初版因此把 3 项误报为差异。必须先**规范化**再比对。
3. **输出路径不能依赖容器内深度**：初版 `Path(__file__).parents[2]` 在容器里越界。

## 输出

差异**不是失败**：本探针的职责是发现并记录，结论（保留/适配/拒绝）由后续 Gate 给出。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# (标签, SQLite SQL, PostgreSQL SQL, 期望是否语义相同)
CASES: list[tuple[str, str, str]] = [
    ("NULL 升序位置",
     "SELECT group_concat(v) FROM (SELECT v FROM (SELECT NULL AS v UNION ALL SELECT 2 "
     "UNION ALL SELECT 1) ORDER BY v)",
     "SELECT string_agg(v::text, ',') FROM (SELECT v FROM (SELECT NULL::int AS v UNION ALL "
     "SELECT 2 UNION ALL SELECT 1) AS t ORDER BY v) AS s"),
    ("NULL 降序位置",
     "SELECT group_concat(v) FROM (SELECT v FROM (SELECT NULL AS v UNION ALL SELECT 2 "
     "UNION ALL SELECT 1) ORDER BY v DESC)",
     "SELECT string_agg(v::text, ',') FROM (SELECT v FROM (SELECT NULL::int AS v UNION ALL "
     "SELECT 2 UNION ALL SELECT 1) AS t ORDER BY v DESC) AS s"),
    ("整数除法 5/2", "SELECT 5 / 2", "SELECT 5 / 2"),
    ("字符串比较大小写", "SELECT 'A' = 'a'", "SELECT 'A' = 'a'"),
    ("字符串排序大小写",
     "SELECT group_concat(v) FROM (SELECT v FROM (SELECT 'a' AS v UNION ALL SELECT 'B' "
     "UNION ALL SELECT 'c') ORDER BY v)",
     "SELECT string_agg(v, ',') FROM (SELECT v FROM (SELECT 'a' AS v UNION ALL SELECT 'B' "
     "UNION ALL SELECT 'c') AS t ORDER BY v) AS s"),
    # 布尔字面量与整数混用：SQLite 允许，PG 报类型错误（真实差异）
    ("整数与布尔混用", "SELECT 1 = TRUE", "SELECT 1 = TRUE"),
]

JSON_CASES: list[tuple[str, str, str]] = [
    ("JSON 数字取值", "json_extract(payload,'$.n')", "payload->>'n'"),
    ("JSON 字符串取值", "json_extract(payload,'$.s')", "payload->>'s'"),
    ("JSON 布尔取值", "json_extract(payload,'$.b')", "payload->>'b'"),
    ("JSON null 取值", "json_extract(payload,'$.z')", "payload->>'z'"),
]


def _norm(value: object) -> str:
    """规范化：布尔与整数统一成 0/1；None 统一成 NULL。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def sqlite_run() -> tuple[list[str], list[str]]:
    c = sqlite3.connect(":memory:")
    base = []
    for _label, sq, _pg in CASES:
        try:
            base.append(_norm(c.execute(sq).fetchone()[0]))
        except Exception as exc:  # noqa: BLE001
            base.append(f"ERR:{type(exc).__name__}")
    c.executescript(
        "CREATE TABLE j (id INTEGER PRIMARY KEY, payload TEXT);"
        "INSERT INTO j VALUES (1,'{\"n\":1,\"s\":\"1\",\"b\":true,\"z\":null}');"
    )
    js = []
    for _label, sq, _pg in JSON_CASES:
        try:
            js.append(_norm(c.execute(f"SELECT {sq} FROM j").fetchone()[0]))
        except Exception as exc:  # noqa: BLE001
            js.append(f"ERR:{type(exc).__name__}")
    # 类型敏感比较：SQLite 的 json_extract 对数字/字符串是类型敏感的
    for expr, label in (("json_extract(payload,'$.n') = 1", "JSON 数字 = 1"),
                        ("json_extract(payload,'$.s') = 1", "JSON 字符串 = 1")):
        try:
            js.append(f"{label}:{_norm(c.execute(f'SELECT {expr} FROM j').fetchone()[0])}")
        except Exception as exc:  # noqa: BLE001
            js.append(f"{label}:ERR:{type(exc).__name__}")
    c.executescript("CREATE TABLE u (id INTEGER PRIMARY KEY, k TEXT UNIQUE);")
    try:
        c.executescript("INSERT INTO u(k) VALUES (NULL),(NULL);")
        js.append(f"唯一列多个NULL:接受{c.execute('SELECT count(*) FROM u').fetchone()[0]}")
    except Exception as exc:  # noqa: BLE001
        js.append(f"唯一列多个NULL:ERR:{type(exc).__name__}")
    return base, js


def pg_run(dsn: str) -> tuple[list[str], list[str]]:
    import psycopg
    conn = psycopg.connect(dsn)
    base = []
    for _label, _sq, pg in CASES:
        try:
            base.append(_norm(conn.execute(pg).fetchone()[0]))
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            base.append(f"ERR:{type(exc).__name__}")
    conn.execute("DROP TABLE IF EXISTS dm_j, dm_u")
    conn.execute("CREATE TABLE dm_j (id int primary key, payload jsonb)")
    conn.execute("INSERT INTO dm_j VALUES (1,'{\"n\":1,\"s\":\"1\",\"b\":true,\"z\":null}')")
    conn.commit()
    js = []
    for _label, _sq, pg in JSON_CASES:
        try:
            js.append(_norm(conn.execute(f"SELECT {pg} FROM dm_j").fetchone()[0]))
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            js.append(f"ERR:{type(exc).__name__}")
    # 类型敏感比较：PG 用 jsonb 对 jsonb 保留类型（与 SQLite 的 json_extract 等价）
    for expr, label in (
        ("(payload -> 'n') = '1'::jsonb", "JSON 数字 = 1"),
        ("(payload -> 's') = '1'::jsonb", "JSON 字符串 = 1"),
    ):
        try:
            js.append(f"{label}:{_norm(conn.execute(f'SELECT {expr} FROM dm_j').fetchone()[0])}")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            js.append(f"{label}:ERR:{type(exc).__name__}")
    conn.execute("CREATE TABLE dm_u (id serial primary key, k text UNIQUE)")
    try:
        conn.execute("INSERT INTO dm_u(k) VALUES (NULL),(NULL)")
        conn.commit()
        js.append(f"唯一列多个NULL:接受{conn.execute('SELECT count(*) FROM dm_u').fetchone()[0]}")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        js.append(f"唯一列多个NULL:ERR:{type(exc).__name__}")
    conn.execute("DROP TABLE IF EXISTS dm_j, dm_u")
    conn.commit()
    conn.close()
    return base, js


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

    sq_base, sq_json = sqlite_run()
    pg_base, pg_json = pg_run(dsn)
    sq = sq_base + sq_json
    pg = pg_base + pg_json
    labels = ([c[0] for c in CASES]
              + [c[0] for c in JSON_CASES]
              + ["JSON 数字 = 1", "JSON 字符串 = 1", "唯一列多个 NULL"])

    diffs: list[str] = []
    print(f"{'维度':26s} {'SQLite':16s} {'PostgreSQL':16s} 一致")
    for label, a, b in zip(labels, sq, pg, strict=True):
        same = a == b
        if not same:
            diffs.append(label)
        print(f"  {label:24s} {a:16s} {b:16s} {'是' if same else '否'}")

    print()
    print(f"语义一致：{len(labels) - len(diffs)}/{len(labels)}；差异：{len(diffs)}")
    for d in diffs:
        print(f"  - {d}")

    out_dir = Path(os.environ.get("MIGRATION_PROOF_OUT", "artifacts/migration-proof"))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        lines = ["# Gate 1：方言语义矩阵（两库真实执行，语义比对）", "",
                 "由 `scripts/migration-proof/dialect_matrix_probe.py` 生成。",
                 "结果已规范化（布尔→0/1，None→NULL），避免把 repr 差异误报为语义差异。", "",
                 "| 维度 | SQLite | PostgreSQL | 语义一致 |", "|---|---|---|---|"]
        for label, a, b in zip(labels, sq, pg, strict=True):
            lines.append(f"| {label} | `{a}` | `{b}` | {'是' if a == b else '**否**'} |")
        lines += ["", f"语义一致 {len(labels) - len(diffs)}/{len(labels)}。", ""]
        if diffs:
            lines += ["## 差异项（需逐项给出保留/适配/拒绝结论）", ""]
            lines += [f"- {d}" for d in diffs]
            lines += ["", "本探针只负责**发现并记录**；结论由后续 Gate 给出。", ""]
        (out_dir / "dialect-matrix.md").write_text("\n".join(lines) + "\n")
        print(f"证据：{out_dir / 'dialect-matrix.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据文件（{exc}）")
    # 差异不是失败：本探针负责发现，不负责判定可接受性
    return 0


if __name__ == "__main__":
    sys.exit(main())
