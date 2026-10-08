"""守护：不得在 `with write_transaction(...) as session` 内用**外层** `db` 取锁。

## 为什么这是安全要求（实测生产死锁）

`write_transaction` 打开的是**新连接**。若在其内部用外层 `db` 去取同一把计数行锁：

```text
内层 session  持计数行锁  →  等本函数返回
外层 db       等计数行锁（同线程，不同连接）
```

PostgreSQL 只看到「外层等内层」这一条边，**看不到环**（内层并未等任何数据库锁，
它在等应用代码），因此**永不检测、永不超时**。

实测（生产 2026-10-08）：4 个 steward 作业冻结 3.5 小时，4 条连接停在
`idle in transaction` 持有 `agent_capacity_counters` 行锁，协调器与 `reaper_pass`
全部阻塞——整套 steward 永久停摆，且无任何错误日志。

本测试用 AST 静态检查这类误用，把缺陷提前到单测阶段。
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

#: 会在数据库侧取锁/写计数行的函数；在 `write_transaction` 块内必须传内层 session。
LOCKING_CALLS = frozenset(
    {
        "release_job",
        "release_attempt",
        "release_account_run",
        "release_gate",
        "acquire",
        "try_acquire",
        "set_active",
        "ensure_counter",
    }
)


def test_no_outer_db_locking_call_inside_write_transaction() -> None:
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover
            continue
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef):
                continue
            params = {a.arg for a in func.args.args + func.args.kwonlyargs}
            if "db" not in params:
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.With):
                    continue
                if not any(isinstance(it.optional_vars, ast.Name) for it in node.items):
                    continue
                for inner in ast.walk(node):
                    if not isinstance(inner, ast.Call):
                        continue
                    name = getattr(inner.func, "attr", None) or getattr(inner.func, "id", None)
                    if name not in LOCKING_CALLS:
                        continue
                    if inner.args and isinstance(inner.args[0], ast.Name):
                        if inner.args[0].id == "db":
                            offenders.append(f"{path.name}:{inner.lineno} {name}(db, ...)")
    assert not offenders, (
        "在 write_transaction 块内用外层 db 取锁会自我死锁（PG 检测不到）：\n  "
        + "\n  ".join(offenders)
    )
