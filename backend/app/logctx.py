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

# 请求级上下文：request_id 与当前认证用户（由依赖项回填）
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
user_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("user_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "user_id": user_id_var.get(),
            "request_id": request_id_var.get(),
        }
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
