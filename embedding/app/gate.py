"""有界推理闸门：本服务最重要的安全机制。

## 要解决的三个问题

### 1. 并发上界

这台机器是 **4 核且同时运行生产与开发**。若不限制，多租户并发请求会让 ONNX
自行开满线程并争抢 CPU。闸门把「同时有多少份推理在跑」钉死在 `max_concurrent`
（默认 1）。

### 2. 有界排队

并发为 1 时，请求会排队。**队列必须有界**：无界队列会把「资源不足」变成
「内存增长 + 全部请求超时」，且故障期间队列越长、恢复越慢。
队列满时立即拒绝（快速失败），而不是接受后必然超时。

### 3. 名额在推理**真正结束**后释放（最容易被写错的一点）

```python
# 错误：超时即释放名额，但线程里的推理还在跑
async with sem:
    await asyncio.wait_for(to_thread(infer), timeout=...)
# -> 超时后 sem 已释放，下一个请求立刻开始，而旧推理仍在占用 CPU
# -> 「并发 1」变成「并发无限」，只是对外表现为部分超时
```

因此本闸门的形态是：**占用、推理、释放在同一个工作线程内顺序完成**；
`wait_timeout` 只约束**等待名额**的时间，**不**约束推理本身。

推理一旦开始就跑到结束，名额在结束后才释放。这保证任一时刻真正在跑的推理
不超过 `max_concurrent`。

## 优先级

`online`（用户提问触发的查询编码）优先于 `background`（后台文档索引）。
理由：后台索引是**可延迟**工作，而在线查询有用户在等。若不加区分，一次全库
重建会让所有在线查询排队到超时。

## 为什么用 threading 而不是 asyncio

ONNX Runtime 的 `InferenceSession.run` 是同步阻塞调用。用 `asyncio` 原语包一层
再放进线程，会引入「谁在哪个线程释放」的跨线程状态问题。这里直接用
`threading.Condition`，并让 acquire/infer/release 都在**同一个线程**里发生，
状态归属清晰。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

#: 优先级。数字越小越优先。
PRIORITY_ONLINE = 0
PRIORITY_BACKGROUND = 1

_PRIORITY_NAMES = {PRIORITY_ONLINE: "online", PRIORITY_BACKGROUND: "background"}


class QueueFull(Exception):
    """等待队列已满：立即拒绝，不排队。

    这是**背压**，不是故障。调用方应返回 503 并让请求方稍后重试（或对后台索引
    直接跳过本轮，下一轮维护再补）。
    """


class SlotTimeout(Exception):
    """在 `wait_timeout` 内未取得名额。

    与 `QueueFull` 的区别：请求曾进入队列但没等到。两者对调用方都是「稍后重试」，
    但运维含义不同（队列容量 vs 处理能力）。
    """


@dataclass
class GateMetrics:
    """闸门观测值。全部为计数器，不含文本或标识。"""

    active: int = 0
    waiting_online: int = 0
    waiting_background: int = 0
    completed: int = 0
    rejected_queue_full: int = 0
    rejected_timeout: int = 0
    last_duration_ms: float | None = None
    max_observed_active: int = 0
    total_inference_ms: float = 0.0
    _durations: deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def summary(self) -> dict[str, object]:
        durations = sorted(self._durations)
        return {
            "active": self.active,
            "waiting_online": self.waiting_online,
            "waiting_background": self.waiting_background,
            "completed": self.completed,
            "rejected_queue_full": self.rejected_queue_full,
            "rejected_timeout": self.rejected_timeout,
            "last_duration_ms": self.last_duration_ms,
            # max_observed_active 是「并发上界是否真的成立」的**直接证据**：
            # 任何时刻它都不得大于配置的 max_concurrent。
            "max_observed_active": self.max_observed_active,
            "p50_duration_ms": durations[len(durations) // 2] if durations else None,
            "p95_duration_ms": (
                durations[min(len(durations) - 1, int(len(durations) * 0.95))]
                if durations
                else None
            ),
        }


class InferenceGate:
    """并发上界 + 有界排队 + 优先级。线程安全。"""

    def __init__(
        self,
        *,
        max_concurrent: int,
        max_waiting: int,
        wait_timeout_seconds: float,
    ) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent 必须 >= 1")
        if max_waiting < 0:
            raise ValueError("max_waiting 不能为负")
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds 必须为正")

        self._max_concurrent = max_concurrent
        self._max_waiting = max_waiting
        self._wait_timeout = wait_timeout_seconds

        self._cond = threading.Condition()
        self._active = 0
        # 每个等待者是一个 ticket；释放时唤醒优先级最高的 ticket。
        self._tickets: dict[int, int] = {}  # ticket id -> priority
        self._next_ticket = 0
        self._metrics = GateMetrics()

    # ---- 状态查询 --------------------------------------------------

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def max_waiting(self) -> int:
        return self._max_waiting

    def metrics(self) -> GateMetrics:
        with self._cond:
            snapshot = GateMetrics(
                active=self._metrics.active,
                waiting_online=self._metrics.waiting_online,
                waiting_background=self._metrics.waiting_background,
                completed=self._metrics.completed,
                rejected_queue_full=self._metrics.rejected_queue_full,
                rejected_timeout=self._metrics.rejected_timeout,
                last_duration_ms=self._metrics.last_duration_ms,
                max_observed_active=self._metrics.max_observed_active,
                total_inference_ms=self._metrics.total_inference_ms,
            )
            snapshot._durations = deque(self._metrics._durations, maxlen=200)
            return snapshot

    def _recount_waiting_locked(self) -> None:
        online = background = 0
        for priority in self._tickets.values():
            if priority == PRIORITY_ONLINE:
                online += 1
            else:
                background += 1
        self._metrics.waiting_online = online
        self._metrics.waiting_background = background

    # ---- 取得 / 释放 ------------------------------------------------

    def _acquire(self, priority: int, deadline: float) -> int:
        """取得名额，返回 ticket id。失败抛 `QueueFull` / `SlotTimeout`。"""
        with self._cond:
            # 有空位：直接进入，不排队（避免「明明空闲却仍计入等待」）。
            if self._active < self._max_concurrent and not self._tickets:
                self._active += 1
                self._metrics.active = self._active
                self._metrics.max_observed_active = max(
                    self._metrics.max_observed_active, self._active
                )
                return -1

            if len(self._tickets) >= self._max_waiting:
                self._metrics.rejected_queue_full += 1
                raise QueueFull(
                    f"等待队列已满（{self._max_waiting}）：并发 {self._max_concurrent}"
                )

            ticket = self._next_ticket
            self._next_ticket += 1
            self._tickets[ticket] = priority
            self._recount_waiting_locked()

            while True:
                # 被唤醒后重新竞争：只让优先级最高的等待者进入，
                # 防止唤醒风暴把名额给到低优先级。
                if self._active < self._max_concurrent:
                    best = min(
                        self._tickets,
                        key=lambda t: (self._tickets[t], t),
                    )
                    if best == ticket:
                        del self._tickets[ticket]
                        self._recount_waiting_locked()
                        self._active += 1
                        self._metrics.active = self._active
                        self._metrics.max_observed_active = max(
                            self._metrics.max_observed_active, self._active
                        )
                        return ticket
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    del self._tickets[ticket]
                    self._recount_waiting_locked()
                    self._metrics.rejected_timeout += 1
                    raise SlotTimeout(
                        f"在 {self._wait_timeout}s 内未取得推理名额"
                    )
                self._cond.wait(timeout=min(remaining, 0.25))

    def _release(self, duration_ms: float) -> None:
        with self._cond:
            self._active -= 1
            self._metrics.active = self._active
            self._metrics.completed += 1
            self._metrics.last_duration_ms = duration_ms
            self._metrics.total_inference_ms += duration_ms
            self._metrics._durations.append(duration_ms)
            self._cond.notify_all()

    @contextmanager
    def slot(self, *, priority: int = PRIORITY_ONLINE) -> Iterator[None]:
        """占用名额并在退出时释放。

        ## 关键语义

        进入 `with` 之后才开始推理，退出时才释放。因此**超时不发生在推理过程中**
        ——超时只可能发生在 `__enter__`（等待名额）。这保证「并发上界」对**实际
        在跑的推理**成立，而不是只对「已返回的请求」成立。
        """
        if priority not in _PRIORITY_NAMES:
            raise ValueError(f"未知优先级：{priority}")
        deadline = time.monotonic() + self._wait_timeout
        self._acquire(priority, deadline)
        started = time.monotonic()
        try:
            yield
        finally:
            # 无论推理成功、失败还是抛异常，名额都必须释放——否则一次失败会
            # 永久占用名额，服务变成「永久繁忙」。
            self._release((time.monotonic() - started) * 1000.0)
