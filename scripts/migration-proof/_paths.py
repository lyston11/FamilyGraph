"""迁移证明扫描器的共享路径规则。

## 为什么需要共享

`build_dialect_blockers.py` 曾用 `BACKEND.rglob("*.py")` 只排除 `__pycache__`，
因此**扫进了 `backend/.venv/`**（第三方库，如 `PIL/PdfParser.py` 里的 `strftime`）。
后果不是多几个噪声条目，而是**计数被污染**：`code` 从 5 变成 23，把「仓库自身的
方言风险」淹没在依赖库里，读数会随 venv 内容变化。

这类缺陷与「ROOT 解析错到 `.trellis`」是同一类：扫描器**静默**给出错误基数。
因此排除规则必须集中在一处，不能每个脚本各写一份。
"""
from __future__ import annotations

from pathlib import Path

# 非仓库源码目录：依赖、缓存、构建产物、虚拟环境
EXCLUDED_PARTS = frozenset({
    ".venv", "venv", "site-packages", "dist-packages", "__pycache__",
    "node_modules", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "build", "dist", ".tox", "migrations_backup",
})


def is_source_file(path: Path) -> bool:
    """路径是否为仓库自身的 Python 源文件（排除依赖/缓存/测试目录）。"""
    parts = set(path.parts)
    return not (parts & EXCLUDED_PARTS)


def source_files(root: Path, pattern: str = "*.py", *, include_tests: bool = False) -> list[Path]:
    """按稳定顺序返回仓库自身的源文件。"""
    out = []
    for p in sorted(root.rglob(pattern)):
        if not p.is_file() or not is_source_file(p):
            continue
        rel = str(p)
        if not include_tests and ("/tests/" in rel or p.name.startswith("test_")):
            continue
        out.append(p)
    return out
