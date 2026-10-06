"""扫描 SQLite 专属构造，并区分「真实代码」与「注释/文档字符串/测试」。

初版扫描把注释里的 `json_extract` 也计入，产生 16 处虚假命中；Gate 1 要求
可复核，因此必须用 AST 把注释、docstring 和字符串字面量分开。
"""
from __future__ import annotations
import ast, json, subprocess
from pathlib import Path


def _repo_root() -> Path:
    out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True,
                                  cwd=Path(__file__).parent).strip()
    return Path(out)


ROOT = _repo_root()
import os as _os

# 证据输出目录：可用 MIGRATION_PROOF_OUT 覆盖（任务归档后指向持久位置）。
OUT_DIR = Path(_os.environ.get(
    "MIGRATION_PROOF_OUT",
    str(Path(__file__).resolve().parents[2]
        / ".trellis/tasks/10-05-migration-proof-gates/research/evidence"),
))
BACKEND = ROOT / "backend"
OUT = OUT_DIR

TOKENS = ("json_extract", "strftime", "julianday", "last_insert_rowid", "fts5", "AUTOINCREMENT")


def _string_literals(tree: ast.AST) -> set[int]:
    """返回所有字符串字面量的行号（含 docstring）。"""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lines.add(node.lineno)
    return lines


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                start = body[0].lineno
                end = body[0].end_lineno or start
                lines.update(range(start, end + 1))
    return lines


def scan() -> list[dict]:
    findings = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        src = path.read_text(errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        doc_lines = _docstring_lines(tree)
        str_lines = _string_literals(tree)
        comment_lines = {
            n for n, line in enumerate(src.splitlines(), 1)
            if line.lstrip().startswith("#") or "#" in line.split('"')[0].split("'")[0]
        }
        rel = str(path.relative_to(ROOT))
        for n, line in enumerate(src.splitlines(), 1):
            for tok in TOKENS:
                if tok not in line:
                    continue
                if n in doc_lines:
                    kind = "docstring"
                elif n in comment_lines:
                    kind = "comment"
                elif n in str_lines:
                    kind = "string-literal"
                else:
                    kind = "code"
                if "/tests/" in rel or path.name.startswith("test_"):
                    kind = "test"
                findings.append({
                    "id": f"SQL-{tok}-{rel}:{n}",
                    "token": tok, "path": rel, "line": n, "kind": kind,
                    "text": line.strip()[:200],
                })
    return findings


def main() -> None:
    items = scan()
    by_kind: dict[str, int] = {}
    by_token: dict[str, int] = {}
    for x in items:
        by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
        if x["kind"] in ("code", "string-literal"):
            by_token[x["token"]] = by_token.get(x["token"], 0) + 1
    (OUT / "dialect-blockers.json").write_text(
        json.dumps({"by_kind": by_kind, "real_code_by_token": by_token, "items": items},
                   ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"by_kind": by_kind, "real_code_by_token": by_token}, ensure_ascii=False))
    print("--- 真实代码命中（非注释/非测试）---")
    for x in items:
        if x["kind"] in ("code", "string-literal") and "tests/" not in x["path"]:
            print(f'  {x["path"]}:{x["line"]} [{x["kind"]}] {x["text"][:110]}')


if __name__ == "__main__":
    main()
