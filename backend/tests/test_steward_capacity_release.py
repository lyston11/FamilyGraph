"""C2/C9：steward job 名额必须在**真实结算路径**归还。

## 为什么单独立这个文件

`steward.settle_steward_job` 里有一行 `capacity.release_job(...)`，看起来正确，
但**生产不经过它**。真实路径是：

```
steward_runtime._execute
  -> steward.execute_steward_job
    -> run_steward_job
      -> _publish_success        -> steward_pipeline.publish          (成功)
      -> steward_pipeline.record_failure                              (失败)
```

因此那行是**死代码**，而真实路径一个都不归还。实测 dev 后果：40 个 job
`capacity_acquired_at` 非空、`capacity_released_at` 全空，counter 停在 `active=1`，
该 space 之后**再也无法入队**——容量被自己占满，且没有任何错误可见。

## 本文件断言的是**路径**，不是行为

只断言「release_job 被调用过」不足以防止回归：有人可以把它加回死代码路径。
因此断言的是 `steward_pipeline.publish` / `record_failure` 的**源码**里存在归还，
并断言 `settle_steward_job` 不是唯一持有归还的地方。
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.services.steward_pipeline as pipeline_mod

_SRC = Path(pipeline_mod.__file__).read_text()


def _releases_in(func_name: str) -> list[int]:
    """返回某函数体内 `capacity.release_job` 调用的行号。"""
    tree = ast.parse(_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            lines = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if (
                        isinstance(fn, ast.Attribute)
                        and fn.attr == "release_job"
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id == "capacity"
                    ):
                        lines.append(sub.lineno)
            return lines
    return []


def test_publish_releases_the_job_slot():
    """成功路径（`publish`）必须归还。

    这是生产上**唯一**的成功终态路径；漏掉它每个成功的 job 都泄漏一个名额。
    """
    assert _releases_in("publish"), (
        "steward_pipeline.publish 未归还 job 名额——"
        "这是真实成功路径，漏掉会让 counter 永久停在 active>0"
    )


def test_record_failure_releases_on_terminal_status():
    """失败终态必须归还；但**重试路径（queued）不得归还**。"""
    lines = _releases_in("record_failure")
    assert lines, "steward_pipeline.record_failure 未归还 job 名额（终态泄漏）"
    # 归还必须出现在 `else:`（终态）分支内，不能出现在重试分支
    src_lines = _SRC.splitlines()
    for line_no in lines:
        # 向上找到最近的 `else:` 判断
        window = "\n".join(src_lines[max(0, line_no - 12) : line_no])
        assert 'job.status, job.settled_at = "failed", now' in window, (
            f"第 {line_no} 行的归还不位于终态分支内——"
            "若落在重试分支（status='queued'）会让重试期间名额凭空多出"
        )


def test_release_is_not_only_in_the_dead_path():
    """`steward.settle_steward_job` 不得是唯一持有归还的地方。

    该函数在生产上不可达（真实路径走 `steward_pipeline`），因此「它里面有归还」
    不能作为正确性证据。
    """
    import app.services.steward as steward_mod

    steward_src = Path(steward_mod.__file__).read_text()
    assert "capacity.release_job" in steward_src, "settle_steward_job 的归还被移除了？"
    # 关键：真实路径也必须归还
    assert _releases_in("publish"), "真实路径未归还，而只有死代码路径归还"
