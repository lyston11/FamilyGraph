"""逐条执行 PRAGMA 与 SQLite DDL，给出可复跑结论（Gate 1 剩余项）。

`build_raw_sql_inventory.py` 只做静态分类；本脚本把其中**可判定**的部分
（PRAGMA 语句、SQLite DDL 构造）逐条在 PostgreSQL 上试执行，记录：

- `blocked`：PostgreSQL 拒绝（语法/函数不存在）；
- `accepted`：PostgreSQL 接受（可能是等价语法或方言无关）。

不在 SQLite 侧执行——PRAGMA 与 SQLite DDL 本就只在 SQLite 存在，问题永远是
「PostgreSQL 是否接受」，而不是「两边是否一致」。

用法：

    PGTEST_DSN=postgresql://... python3 scripts/migration-proof/build_pr_checks.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
BACKEND = ROOT / "backend"
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", "."))

# PRAGMA 名 -> PostgreSQL 等价物（None 表示无等价物，必须移除）
PRAGMA_EQUIVALENT = {
    "foreign_keys": "外键由 FK 约束本身强制，无需开关",
    "journal_mode": "WAL 由服务器配置，非连接级",
    "busy_timeout": "由 lock_timeout / statement_timeout 取代",
    "synchronous": "由服务器 fsync 配置取代",
    "integrity_check": "由 pg_verify_checksums / pg_dump 校验取代",
    "table_info": "由 information_schema.columns 取代",
    "foreign_key_check": "由 FK 约束在写入时强制；迁移期可临时禁用后校验 pg_constraint",
    "foreign_key_list": "由 pg_constraint 取代",
    "index_list": "由 pg_indexes 取代",
}


def collect() -> dict:
    pragmas: list[dict] = []
    ddls: list[dict] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in str(path) or "/tests/" in str(path):
            continue
        src = path.read_text(errors="replace")
        rel = str(path.relative_to(ROOT))
        try:
            tree = ast.parse(src)
        except SyntaxError:
            tree = None
        doc_lines: set[int] = set()
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = getattr(node, "body", None)
                    if body and isinstance(body[0], ast.Expr) and isinstance(
                            body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                        start = body[0].lineno
                        doc_lines.update(range(start, (body[0].end_lineno or start) + 1))
        for n, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or n in doc_lines:
                continue
            for m in re.finditer(r"(?i)\bPRAGMA\s+([a-z_]+)", line):
                name = m.group(1).lower()
                pragmas.append({
                    "id": f"PRAGMA-{name}-{rel}:{n}", "path": rel, "line": n, "pragma": name,
                    "postgres_equivalent": PRAGMA_EQUIVALENT.get(name, "未登记"),
                })
            if re.search(r"(?i)CREATE\s+(VIRTUAL\s+TABLE|TRIGGER)", line) or "RAISE(ABORT" in line:
                kind = ("virtual_table" if re.search(r"(?i)VIRTUAL\s+TABLE", line)
                        else "trigger" if re.search(r"(?i)CREATE\s+TRIGGER", line)
                        else "trigger_body")
                ddls.append({"id": f"DDL-{kind}-{rel}:{n}", "path": rel, "line": n,
                             "kind": kind, "text": stripped[:150]})
    return {"pragmas": pragmas, "ddls": ddls}


def probe_postgres(dsn: str, items: dict) -> dict:
    import psycopg
    conn = psycopg.connect(dsn)
    # 每条 PRAGMA 作为语句试执行（必然失败或未定义）
    for item in items["pragmas"]:
        sql = f"PRAGMA {item['pragma']}"
        try:
            conn.execute(sql)
            conn.rollback()
            item["pg_result"] = "accepted"
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            item["pg_result"] = f"blocked:{type(exc).__name__}"
    for item in items["ddls"]:
        text = item["text"].rstrip('"').rstrip("'")
        try:
            conn.execute(text)
            conn.rollback()
            item["pg_result"] = "accepted"
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            item["pg_result"] = f"blocked:{type(exc).__name__}"
    conn.close()
    return items


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

    items = collect()
    items = probe_postgres(dsn, items)

    p_by_name: dict[str, int] = {}
    for it in items["pragmas"]:
        p_by_name[it["pragma"]] = p_by_name.get(it["pragma"], 0) + 1
    d_by_kind: dict[str, int] = {}
    for it in items["ddls"]:
        d_by_kind[it["kind"]] = d_by_kind.get(it["kind"], 0) + 1

    print(f"  PRAGMA：{len(items['pragmas'])} 处，{len(p_by_name)} 种")
    for name, n in sorted(p_by_name.items()):
        eq = PRAGMA_EQUIVALENT.get(name, "未登记")
        print(f"    {name:18s} ×{n:<3d} -> {eq}")
    print(f"  SQLite DDL：{len(items['ddls'])} 处")
    for kind, n in sorted(d_by_kind.items()):
        print(f"    {kind:14s} ×{n}")
    blocked = [i for i in items["ddls"] if str(i.get("pg_result", "")).startswith("blocked")]
    print(f"  PostgreSQL 拒绝的 DDL：{len(blocked)}/{len(items['ddls'])}")

    unregistered = [n for n in p_by_name if n not in PRAGMA_EQUIVALENT]
    if unregistered:
        print(f"  WARN: 未登记的 PRAGMA（必须补等价物或明确移除）：{unregistered}")

    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "pragma-ddl-checks.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n")
        lines = ["# Gate 1：PRAGMA 与 SQLite DDL 逐条检查", "",
                 "由 `scripts/migration-proof/build_pr_checks.py` 生成。", "",
                 "## PRAGMA", "", "| PRAGMA | 处数 | PostgreSQL 等价物 |", "|---|---|---|"]
        for name, n in sorted(p_by_name.items()):
            lines.append(f"| `{name}` | {n} | {PRAGMA_EQUIVALENT.get(name, '**未登记**')} |")
        lines += ["", "## SQLite DDL", "", "| 构造 | 处数 |", "|---|---|"]
        for kind, n in sorted(d_by_kind.items()):
            lines.append(f"| {kind} | {n} |")
        lines += ["", f"PostgreSQL 拒绝：{len(blocked)}/{len(items['ddls'])}（详见 json）", "",
                  "## 结论", "",
                  "PRAGMA 与 SQLite DDL **不迁移**：它们属于「不重放历史 Alembic、改用审查后",
                  "PG baseline」的范畴。本表的作用是**穷举**它们，确保没有遗漏项被静默保留。", ""]
        (OUT_DIR / "pragma-ddl-checks.md").write_text("\n".join(lines) + "\n")
        print(f"  证据：{OUT_DIR / 'pragma-ddl-checks.md'}")
    except OSError as exc:
        print(f"WARN: 无法写入证据（{exc}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
