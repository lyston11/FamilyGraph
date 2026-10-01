"""运行时可观测性：连接池等待与事件循环延迟。

为何需要这个模块
----------------

2026-09-30 的三个缺陷排查中，有两个被**误判**，共同原因是缺少产品内诊断：

- 「连接池满」与「事件循环被同步 SQL 钉住」在现象上都是「请求变慢 / 心跳失败」，
  但成因完全不同，处置也完全不同。没有指标时只能靠外部脚本 + `py-spy` + journalctl
  事后拼接，且容易把结果当原因（先后两次把 401 token 过期误判为上游故障与循环阻塞）。
- 池占用只能从进程外近似（数 `/proc/<pid>/fd`），拿不到「等了多久」。

因此本模块提供两个**独立**指标，并在日志里用不同事件名，避免合并成
「响应慢」这类无法处置的结论：

- ``db_pool_wait``：一次 ``engine.connect()`` 在池里等待的时长。池满时它等于
  ``pool_timeout``，是「池耗尽」的直接证据。
- ``event_loop_lag``：事件循环未能按时处理定时器的时长。它变大说明事件循环被
  同步代码占住（会拖垮**所有**端点），与池满（只影响需要连接的请求）是两回事。

两者的区分性由测试保证：仅池满时只有 ``db_pool_wait``，不产生 ``event_loop_lag``。

安全
----

日志字段仅含数值与安全标识（时长、计数、可选路由模板与 run id）；
不含 SQL 语句或参数、请求体、token、凭据、PII（见 logging-guidelines.md 红线）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app import config

logger = logging.getLogger("app.diagnostics")


def note_pool_wait(waited_seconds: float, pool: Any) -> None:
    """池等待超阈值时记一条结构化告警。

    只在超阈值时构造日志参数：热路径上（绝大多数 checkout 不等待）只做一次
    ``perf_counter`` 差值与整数比较，不分配、不加锁。
    """
    waited_ms = int(waited_seconds * 1000)
    if waited_ms < config.DB_POOL_WAIT_WARN_MS:
        return
    logger.warning(
        "db_pool_wait",
        extra={
            "event": "db_pool_wait",
            "waited_ms": waited_ms,
            # 占用快照：等待结束时看到的池状态。它说明「池当时是否真的满」，
            # 而不是让读者从 waited_ms 反推。
            "checkedout": pool.checkedout(),
            "size": pool.size(),
            "overflow": pool.overflow(),
        },
    )


async def event_loop_lag_probe(stop: asyncio.Event) -> None:
    """周期性测量事件循环的调度延迟，超阈值记告警。

    用 ``sleep`` 的实际耗时减去请求的间隔：差值就是「事件循环多久没能按时跑这个
    定时器」，即被同步代码占住的时长。这比 ``call_soon`` 往返更贴近真实影响
    （其它定时器同样会被推迟），且不需要额外线程。

    探针只运行在事件循环上、不取数据库连接、不使用工作线程——因此**池满时它仍能
    记录**（池满阻塞的是工作线程，不是事件循环）。这一点是本模块能在事故现场
    工作的前提。
    """
    interval = config.EVENT_LOOP_LAG_INTERVAL_S
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        started = loop.time()
        try:
            # 用 wait_for 以便 stop 能及时打断，同时保持间隔语义。
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        lag_ms = int((loop.time() - started - interval) * 1000)
        if lag_ms >= config.EVENT_LOOP_LAG_WARN_MS:
            logger.warning(
                "event_loop_lag",
                extra={
                    "event": "event_loop_lag",
                    "lag_ms": lag_ms,
                    "interval_ms": int(interval * 1000),
                },
            )


def start_event_loop_probe() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """启动探针；调用方负责在退出时 set stop 并 await 任务。"""
    stop = asyncio.Event()
    task = asyncio.create_task(event_loop_lag_probe(stop))
    return stop, task


def now_seconds() -> float:
    """便于测试注入的单调时钟读取点。"""
    return time.perf_counter()
