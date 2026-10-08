"""协调器失败必须记录**根因**（traceback），而不只是异常类型名。

## 为什么这是可诊断性要求

`RequiredTargetFailed` 是一个**包装异常**：它的根因在 `__cause__`/`__context__` 里
（例如 `snapshot_budget`、`retry_budget_exhausted`，或底层 provider 错误）。
`logger.warning(..., error=%s, type(exc).__name__)` 只留下 `RequiredTargetFailed`，
排查时无法区分「预算按设计拒绝」与「真的执行失败」。

实测（生产 2026-10-08）：日志只有 `steward coordinator stopped job=17256
error=RequiredTargetFailed`，而 `steward_view_targets.reason_code` 显示
`retry_budget_exhausted`——两者都成立，但日志本身不足以判断。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app/services/steward_runtime.py"


def test_coordinator_failure_logs_exc_info() -> None:
    """`_execute` 的 except 分支必须传 `exc_info=True`。"""
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_execute":
            for sub in ast.walk(node):
                if not isinstance(sub, ast.ExceptHandler):
                    continue
                for call in ast.walk(sub):
                    if not isinstance(call, ast.Call):
                        continue
                    fn = call.func
                    if (getattr(fn, "attr", None) or getattr(fn, "id", None)) != "warning":
                        continue
                    kwargs = {kw.arg: kw.value for kw in call.keywords}
                    assert "exc_info" in kwargs, (
                        "协调器失败日志缺少 exc_info——根因（__cause__）会被丢弃，"
                        "只剩异常类型名，无法区分预算拒绝与真实失败"
                    )
                    value = kwargs["exc_info"]
                    assert isinstance(value, ast.Constant) and value.value is True
                    return
    raise AssertionError("未找到 _execute 中的 logger.warning 调用")
