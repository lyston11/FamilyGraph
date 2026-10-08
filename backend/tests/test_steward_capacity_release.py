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


# ---------------------------------------------------------------- 锁序（C2）


def test_counter_lock_is_taken_before_the_job_row_lock():
    """`_lock_quota_counter` 必须在取 job 行锁**之前**调用。

    ## 为什么这是安全要求（实测死锁）

    修复「归还缺失」后立刻出现真实死锁。`pg_stat_activity` 显示：

    ```
    UPDATE steward_jobs SET capacity_released_at = $1 WHERE id = $2   <- 持 counter，等 job 行锁
    UPDATE steward_jobs SET status = $1 ...                          <- 持 job 行锁，等 counter
    ```

    互等超过 26 分钟，直到手工 `pg_terminate_backend`。

    租约路径的锁序是 `counter -> job`。因此结算路径若先读 job（持其行锁）再取
    counter，就是**反向锁序**，与租约/恢复路径交叉时必然死锁。

    ## 断言方式

    源码行号比较：`_lock_quota_counter` 的调用行号必须**小于**取 job 行锁的
    `require_generation` / `session.get(StewardJob, ...)`。
    """

    def _first_line(func_name: str, needle: str) -> int | None:
        tree = ast.parse(_SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        fn = sub.func
                        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                        if name == needle:
                            return sub.lineno
        return None

    for func_name in ("publish", "record_failure"):
        counter_line = _first_line(func_name, "_lock_quota_counter")
        job_line = _first_line(func_name, "require_generation") or _first_line(func_name, "get")
        assert counter_line is not None, f"{func_name} 未取 counter 行锁（锁序未建立）"
        assert job_line is not None, f"{func_name} 未取 job 行锁？"
        assert counter_line < job_line, (
            f"{func_name}: counter 锁在第 {counter_line} 行、job 行锁在第 {job_line} 行——"
            "反向锁序，与租约路径交叉会真实死锁（实测 26 分钟互等）"
        )


def test_counter_lock_does_not_change_active():
    """`_lock_quota_counter` 只取锁，**不得**改变 `active`。

    若它顺便归还，重试路径（`record_failure` 把 job 放回 `queued`）就会少占一个名额——
    而 `queued` 仍算活跃态，配额会被低估。

    注意：必须检查**代码**而不是文本。初版用字符串包含判断，结果匹配到了 docstring
    里的说明文字（它当然会提到 release），于是误报失败。这里改用 AST。
    """
    tree = ast.parse(_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_lock_quota_counter":
            calls = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    calls.append(getattr(fn, "attr", None) or getattr(fn, "id", None))
            assert "release" not in calls, "_lock_quota_counter 调用了 release——它只应取锁"
            assert "release_job" not in calls, "_lock_quota_counter 调用了 release_job"
            body = _SRC.split("def _lock_quota_counter")[1].split("def publish")[0]
            assert "with_for_update" in body, "未使用 with_for_update——没有真的取锁"
            return
    raise AssertionError("_lock_quota_counter 不存在")
