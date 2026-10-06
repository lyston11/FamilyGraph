"""枚举**事务入口**（不是 helper 定义），并给出计数口径的完整对账。

前两版计数（18、43、46）各自漏了一层：

| 口径 | 计数 | 漏掉了什么 |
|---|---|---|
| 只数 `BEGIN IMMEDIATE` 字面量 | 18 | 经 helper 的调用点 |
| 只数 `immediate=True` + `_immediate_tx` | 43 | `write_transaction` 包装层 |
| AST 数调用表达式 | 46 | 同上，且把 helper 定义计入 |

本脚本显式区分三层，并把 helper 定义排除在入口之外。
"""
from __future__ import annotations
import ast, json, subprocess
from pathlib import Path


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
EV = OUT_DIR

# helper/包装函数：它们**实现**事务边界，不是事务入口
DEFINERS = {
    ("backend/app/commands/context.py", "command_transaction"),
    ("backend/app/commands/context.py", "_begin_immediate"),
    ("backend/app/services/agent_queue.py", "_immediate_tx"),
    ("backend/app/services/steward.py", "_immediate_tx"),
    ("backend/app/services/steward_pipeline.py", "write_transaction"),
}

FORMS = {
    "command_transaction": "command_transaction(..., immediate=True)",
    "immediate_tx": "_immediate_tx(db) 直接调用",
    "write_transaction": "with write_transaction(bind) 包装",
}


def _enclosing(tree: ast.AST, lineno: int) -> str:
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if node.lineno <= lineno <= end and (best is None or node.lineno > best.lineno):
                best = node
    return best.name if best else "?"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in sorted((ROOT / "backend/app").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        src = path.read_text(errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rel = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            text = ast.unparse(node)
            form = None
            if name == "command_transaction" and "immediate=True" in text:
                form = "command_transaction"
            elif name == "_immediate_tx":
                form = "immediate_tx"
            elif name == "write_transaction":
                form = "write_transaction"
            if form is None:
                continue
            fn = _enclosing(tree, node.lineno)
            if (rel, fn) in DEFINERS:
                continue  # helper 内部实现，不是入口
            entries.append({
                "id": f"TX-{form}-{rel}:{node.lineno}",
                "form": form, "path": rel, "line": node.lineno,
                "function": fn, "expression": text[:220],
            })
    by_form: dict[str, int] = {}
    for e in entries:
        by_form[e["form"]] = by_form.get(e["form"], 0) + 1
    out = {
        "total_entries": len(entries),
        "by_form": by_form,
        "reconciliation": {
            "18": "只数 BEGIN IMMEDIATE 字面量：漏掉全部经 helper 的入口",
            "43": "immediate=True + _immediate_tx：漏掉 write_transaction 包装层（23 个调用点）",
            "46": "AST 数调用表达式：仍漏 write_transaction，且把 helper 定义计入",
            "definitive": f"三层入口合计 {len(entries)}；helper 定义已排除",
        },
        "entries": entries,
    }
    (EV / "tx-entries.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"total_entries": len(entries), "by_form": by_form}, ensure_ascii=False))


if __name__ == "__main__":
    main()
