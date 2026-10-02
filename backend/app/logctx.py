"""结构化日志与请求上下文（spec/backend/logging-guidelines.md）。

- JSON 行格式：ts/level/logger/msg/user_id/request_id
- 中间件注入 request_id（uuid4），贯穿单次请求全部日志行
- 脱敏红线：PIN/JWT/pin_hash/challenge 明文永不入日志（测试断言兜底）
"""

import contextvars
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

# 请求级上下文：request_id 与当前认证用户（由依赖项回填）
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
user_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("user_id", default=None)


class JsonFormatter(logging.Formatter):
    """结构化 JSON 行：固定字段 + 调用方通过 ``extra=`` 附带的字段。

    ``extra`` 必须被输出：诊断与计数器（如 ``db_pool_wait`` 的 ``waited_ms``、
    ``event_loop_lag`` 的 ``lag_ms``、``maintenance tick`` 的 ``counters``）都是靠
    ``extra`` 携带的。此前 formatter 只输出下面六个固定键，于是这些值被静默丢弃——
    日志里能看到「事件发生了」却看不到任何数值，诊断等于无效（10-01 实测：
    ``event_loop_lag`` 触发了 4 次，日志行里没有 ``lag_ms``）。

    ``extra`` 优先于固定键：调用方显式附带的字段比默认投影更具体。
    """

    # LogRecord 自带的属性（非 extra）；其余用户附加字段一律输出。
    _RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "user_id": user_id_var.get(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED:
                entry[key] = value
        # traceback 必须输出：`logger.exception(...)` 把堆栈放在 `exc_info` 里，
        # 而 `exc_info` 是 LogRecord 的内部属性，会被上面的循环跳过。此前全仓
        # 所有异常堆栈因此都是空的——2026-10-01 09:02 的进程级故障正是因为
        # 「只知道某路由抛了未处理异常，不知道抛的是什么」而无法定位。
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging() -> None:
    """应用入口统一初始化；DEBUG 默认关闭（logging-guidelines.md）。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # 收敛第三方噪音
    logging.getLogger("uvicorn.access").handlers = [handler]


def new_request_id() -> str:
    return uuid.uuid4().hex
