"""迁移证明脚本的自洽性守护。

这些脚本在真实 PostgreSQL / SQLite 上跑，不在 pytest 里执行，因此它们的
**导入错误只会在真实运行时暴露**——而且往往是在最不合适的时刻（生产切流）。

实测：`real_snapshot_import.py` 用了裸 `text(`，但该文件导入的是
`from sqlalchemy import text as sa_text`，于是「修复序列」步骤抛 `NameError`。
后果是序列从未被修复，dev 切换后 maintenance tick 每轮 `IntegrityError`、
steward 作业完全无法创建——而健康检查仍是 200，完全静默。

本文件用 AST 检查这些脚本引用的名字都确实定义过，把这类错误提前到单测阶段。
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROOF_DIR = ROOT / "scripts" / "migration-proof"


def _proof_scripts() -> list[Path]:
    return sorted(p for p in PROOF_DIR.glob("*.py") if p.name != "_paths.py")


@pytest.mark.parametrize("script", _proof_scripts(), ids=lambda p: p.name)
def test_script_has_no_undefined_global_names(script: Path) -> None:
    """脚本引用的全局名字必须已定义（导入、赋值或内置）。

    只检查 `Name` 在 `Load` 上下文的使用，且排除函数内的局部名。
    这能抓住 `NameError` 类缺陷（拼写错误、别名不一致、漏 import），
    而不会误报参数、局部变量或 comprehension 变量。
    """
    tree = ast.parse(script.read_text())

    # `__file__`/`__name__` 是解释器注入的模块级名字，AST 里看不到定义。
    defined: set[str] = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}
    # 模块级绑定
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            defined.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)

    undefined = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined
    }
    assert not undefined, f"{script.name} 引用了未定义的名字：{sorted(undefined)}"
