"""结构化日志必须输出调用方通过 `extra=` 附带的字段。

为何是回归而不是细节：`JsonFormatter` 原先只输出固定的六个键，静默丢弃所有
`extra`。于是诊断与计数器只能看到「事件发生了」，看不到任何数值——
10-01 实测 `event_loop_lag` 触发 4 次，日志行里没有 `lag_ms`，
`maintenance tick` 也没有 `counters`，诊断实际无效。
"""

from __future__ import annotations

import json
import logging

from app.logctx import JsonFormatter


def _format(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def test_extra_fields_are_emitted() -> None:
    record = logging.LogRecord(
        "app.diagnostics", logging.WARNING, __file__, 1, "db_pool_wait", (), None
    )
    record.event = "db_pool_wait"
    record.waited_ms = 30001
    record.checkedout = 15

    entry = _format(record)
    assert entry["msg"] == "db_pool_wait"
    assert entry["event"] == "db_pool_wait"
    assert entry["waited_ms"] == 30001
    assert entry["checkedout"] == 15


def test_structured_counters_are_emitted() -> None:
    """嵌套结构（如 maintenance 的 counters）同样必须保留。"""
    record = logging.LogRecord(
        "app.services.maintenance", logging.INFO, __file__, 1, "maintenance tick", (), None
    )
    record.counters = {"steward_gc_rows": 32}

    entry = _format(record)
    assert entry["counters"] == {"steward_gc_rows": 32}


def test_base_fields_are_still_present_and_not_duplicated() -> None:
    """基础字段仍存在，且不会把 LogRecord 内部属性（如 levelno/pathname）漏出来。"""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    entry = _format(record)
    assert entry["msg"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.test"
    # `msg` 是格式化后的消息（预期存在）；其余 LogRecord 内部属性不得外泄。
    for internal in ("levelno", "pathname", "lineno", "args", "exc_info"):
        assert internal not in entry, f"内部属性 {internal} 不应出现在日志输出中"
