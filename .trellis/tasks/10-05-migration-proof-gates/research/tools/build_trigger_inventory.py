"""触发器 inventory：以**实际生成的对象**为准，而不是源码行位点。

## 为什么必须这样数

`build_inventory.py` 用 `grep 'CREATE TRIGGER'` 数源码行，得到 14。但其中 4 处位于
`for` 循环内，会按配置矩阵展开。对隔离 DATA_DIR 跑 `alembic upgrade head` 后统计
`sqlite_master`，实际是 **69** 个触发器。

低估 5 倍在迁移语境下是危险的：PG baseline 需要为**每个实际触发器**提供等价物，
按源码行数做清单会静默漏掉 55 个（全部是 `sri_*` steward input revision 计数器，
保护的是投影新鲜度不变量）。

本脚本同时记录两种口径并显式说明差异，避免以后再次混淆。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def _repo_root() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
BACKEND = ROOT / "backend"
EV = ROOT / ".trellis/tasks/10-05-migration-proof-gates/research/evidence"

# 已知的循环展开点：这些源码行会产生多个实际触发器，因此不能按行计数。
LOOP_SITES = {
    "backend/migrations/versions/0045_steward_staged_publication.py:189": "for (table, columns, layer, scope_columns) in _SOURCES × for (event, prefixes)",
    "backend/migrations/versions/0048_steward_terminology_publication.py:117": "for (table, ...) in _SOURCES × for (event, prefixes)",
    "backend/migrations/versions/0047_rag_lifecycle_integrity.py:93": "for (operation, suffix) in (...)",
    "backend/migrations/versions/0014_memory_rag.py:251": "固定 3 个（无循环，但同文件多处）",
}


def source_sites() -> list[dict]:
    sites = []
    for path in sorted((BACKEND / "migrations/versions").glob("*.py")):
        src = path.read_text(errors="replace")
        rel = str(path.relative_to(ROOT))
        for n, line in enumerate(src.splitlines(), 1):
            if "CREATE TRIGGER" in line.upper():
                key = f"{rel}:{n}"
                sites.append({
                    "id": f"TRIGGER-SITE-{rel}:{n}", "path": rel, "line": n,
                    "owner": "backend-migration", "status": "inventory", "evidence": "L0",
                    "expands_in_loop": key in LOOP_SITES,
                    "loop_note": LOOP_SITES.get(key, ""),
                })
    return sites


def actual_triggers() -> tuple[list[dict], str | None]:
    """在隔离 DATA_DIR 上跑迁移，统计 sqlite_master 里的真实触发器。"""
    tmp = tempfile.mkdtemp(prefix="fg-trigger-inv-")
    env = {**os.environ, "DATA_DIR": tmp}
    proc = subprocess.run(
        [str(BACKEND / ".venv/bin/python"), "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        return [], f"alembic upgrade head 失败：{proc.stderr.strip()[:300]}"
    db = Path(tmp) / "db" / "app.db"
    if not db.exists():
        return [], f"迁移成功但未找到 {db}"
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return ([{"id": f"TRIGGER-{name}", "name": name, "table": tbl,
              "owner": "backend-migration", "status": "inventory", "evidence": "L2"}
             for name, tbl in rows], None)


def main() -> int:
    sites = source_sites()
    actual, error = actual_triggers()
    out = {
        "source_sites": len(sites),
        "actual_triggers": len(actual),
        "reconciliation": (
            f"源码行位点 {len(sites)} 个，实际生成 {len(actual)} 个触发器。"
            "差异来自 for 循环展开；按源码行计数会漏掉循环产生的触发器。"
        ),
        "loop_sites": LOOP_SITES,
        "sites": sites,
        "triggers": actual,
        "error": error,
    }
    (EV / "trigger-inventory.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("source_sites", "actual_triggers", "error")},
                     ensure_ascii=False))
    return 0 if not error else 2


if __name__ == "__main__":
    sys.exit(main())
