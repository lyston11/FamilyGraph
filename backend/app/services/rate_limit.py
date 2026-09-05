"""进程内滑动窗口限流（09-05 design.md §3：注册端点 IP 限流）。

compose 单 API 进程、无横向扩容：状态仅存进程内存（每 (scope, key) 一个时间
戳双端队列，全局锁保证线程安全）；未来多实例部署需迁移到共享存储，属后续任务。
登录侧 AD-2 的 name 级锁定（services/auth_guard.py）不受本模块影响。
"""

from __future__ import annotations

import threading
import time
from collections import deque

from app import config
from app.errors import REGISTRATION_RATE_LIMITED, raise_api_error

_REGISTRATION_SCOPE = "registration"


class SlidingWindowLimiter:
    """按 (scope, key) 的滑动窗口计数：窗口内命中数达到上限即拒绝。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def hit_and_check(self, scope: str, key: str, *, limit: int, window_seconds: int) -> int:
        """记录一次命中并判定是否放行。

        返回 Retry-After 秒数（放行返回 0；拒绝返回窗口剩余秒数，至少 1）。
        """
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault((scope, key), deque())
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return max(int(window_seconds - (now - bucket[0])) + 1, 1)
            bucket.append(now)
            return 0

    def reset(self) -> None:
        """清空全部计数（测试隔离用）。"""
        with self._lock:
            self._hits.clear()


registration_limiter = SlidingWindowLimiter()


def check_registration_rate_limit(ip: str | None) -> None:
    """注册端点限流门禁：超限抛 429 + Retry-After（design.md §4 顺序：开关→限流→查重）。"""
    retry_after = registration_limiter.hit_and_check(
        _REGISTRATION_SCOPE,
        ip or "unknown",
        limit=config.REGISTRATION_RATE_LIMIT_MAX_ATTEMPTS,
        window_seconds=config.REGISTRATION_RATE_LIMIT_WINDOW_SECONDS,
    )
    if retry_after:
        raise_api_error(
            429,
            REGISTRATION_RATE_LIMITED,
            "注册请求过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
