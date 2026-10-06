"""结构化 raw SQL inventory：区分「需要迁移审查」与「方言中立」。

初版只给出 1336 条正则命中，既不可读也无法验收。本脚本按**风险类别**归类，
只对真正影响迁移的类别逐条列出稳定 ID，并标注是否已实测。
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import is_source_file  # noqa: E402


def _repo_root() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
import os as _os

# 证据输出目录：可用 MIGRATION_PROOF_OUT 覆盖（任务归档后指向持久位置）。
OUT_DIR = Path(_os.environ.get(
    "MIGRATION_PROOF_OUT",
    str(ROOT / "artifacts/migration-proof"),
))
BACKEND = ROOT / "backend"
EV = OUT_DIR

# 风险类别 -> (正则, 迁移影响)
RISK = {
    # 只匹配**进入 SQL** 的函数。`strftime` 在本仓库是 Python 的 datetime 方法
    # （实测 `datetime.now(UTC).strftime(...)`，不进 SQL），因此不在本类别。
    "sqlite_only_function": (
        r"(?:func\.|sa\.func\.)?(json_extract|last_insert_rowid)\s*\(",
        "PostgreSQL 无此函数：建表或执行时失败",
    ),
    "sqlite_pragma": (r"(?i)\bPRAGMA\s+\w+", "PostgreSQL 无 PRAGMA，需改为 SET / 连接参数"),
    "sqlite_ddl": (r"(?i)CREATE\s+(VIRTUAL\s+TABLE|TRIGGER)|RAISE\s*\(\s*ABORT",
                   "PostgreSQL 语法不同：虚拟表/FTS5 不存在，触发器需 plpgsql"),
    "sqlite_conflict_clause": (r"(?i)INSERT\s+OR\s+(IGNORE|REPLACE)", "需改为 ON CONFLICT"),
    # `autoincrement=True` 是 SQLAlchemy 参数，不是 SQL 关键字：实测 PG 渲染为 SERIAL。
    # 真正不可移植的是字面量 SQL 里的 AUTOINCREMENT 关键字（本仓库为 0 处）。
    # 只匹配**字面量 SQL 关键字**：`autoincrement=True` 是 SQLAlchemy 参数，
    # 实测在 PostgreSQL 上渲染为 SERIAL（可移植）。因此用负向断言排除 `=` 形式。
    "literal_autoincrement_keyword": (r"(?i)(?<![_a-z])AUTOINCREMENT(?!\s*=)(?!\w)",
                                      "字面量 SQL 关键字，PostgreSQL 不支持；本仓库未出现"),
    "transaction_control": (r"(?i)\bBEGIN\s+IMMEDIATE\b", "PostgreSQL 无此语义，需 CAS/行锁/counter"),
    "dialect_neutral_sql": (r"(?i)\b(SELECT|INSERT|UPDATE|DELETE|WITH)\b", "方言中立，仅需索引与计划复核"),
}

EXECUTED = {
    "sqlite_only_function": "部分：pg_replay_probe.py 已实测 0042 json_extract 阻塞；"
                            "3 个运行期查询已在真实 PG 编译并执行成功",
    "sqlite_ddl": "部分：pg_replay_probe.py 已实测 FTS5 与 2 个触发器阻塞",
    "transaction_control": "部分：pg_deadlock_probe.py 已实测反向锁序死锁",
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_risk: dict[str, list[dict]] = {k: [] for k in RISK}
    for path in sorted(BACKEND.rglob("*.py")):
        # 生产代码口径：排除依赖库（.venv 等）**和测试**。
        # 迁移风险看的是会发布出去的代码；测试自身的 PRAGMA 另行统计。
        if not is_source_file(path) or "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        src = path.read_text(errors="replace")
        rel = str(path.relative_to(ROOT))
        comment_lines: set[int] = set()
        try:
            import io
            import tokenize
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.COMMENT:
                    comment_lines.add(tok.start[0])
        except Exception:  # noqa: BLE001 - tokenize 失败时退化为无注释信息
            pass
        try:
            tree = ast.parse(src)
            doc_lines: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = getattr(node, "body", None)
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                            and isinstance(body[0].value.value, str):
                        start = body[0].lineno
                        doc_lines.update(range(start, (body[0].end_lineno or start) + 1))
        except SyntaxError:
            doc_lines = set()
        for n, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or n in doc_lines or n in comment_lines:
                continue  # 整行注释、行尾注释与文档字符串都不是可执行 SQL
            for risk, (pattern, impact) in RISK.items():
                if risk == "dialect_neutral_sql":
                    continue  # 数量太大，只做汇总计数
                if re.search(pattern, line):
                    by_risk[risk].append({
                        "id": f"SQL-{risk}-{rel}:{n}", "risk": risk, "path": rel, "line": n,
                        "text": stripped[:180], "impact": impact,
                        "owner": "backend-sql", "status": "inventory",
                        "evidence": "L0" if risk not in EXECUTED else "L2-partial",
                        "executed": EXECUTED.get(risk, "未实测"),
                    })
    neutral = 0
    for path in sorted(BACKEND.rglob("*.py")):
        if not is_source_file(path) or "/tests/" in str(path):
            continue
        for line in path.read_text(errors="replace").splitlines():
            if re.search(RISK["dialect_neutral_sql"][0], line):
                neutral += 1
    summary = {k: len(v) for k, v in by_risk.items()}
    out = {
        "summary": summary,
        "dialect_neutral_count": neutral,
        "by_risk": by_risk,
        "note": (
            "只列出需要迁移审查的类别；方言中立 SQL 仅计数（其风险是索引与执行计划，"
            "不是方言）。注释与文档字符串已排除。"
        ),
    }
    (EV / "raw-sql-inventory.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"summary": summary, "dialect_neutral": neutral}, ensure_ascii=False))


if __name__ == "__main__":
    main()
