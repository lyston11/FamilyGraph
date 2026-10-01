"""池等待与事件循环延迟：两个独立信号，且必须可区分。

为何是合同而不是细节：09-30 的三个缺陷里有两个被误判，共同原因是「池满」与
「事件循环被占住」在现象上都是「请求变慢 / 心跳失败」。诊断若把两者合并成一个
「响应慢」，就无法处置；本文件用区分性断言固定这条边界。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SATimeoutError

from app import config
from app.db import POOL_MAX_CONNECTIONS, SessionLocal, engine
from app.services import runtime_diagnostics


@pytest.fixture
def short_pool_timeout(monkeypatch):
    """把池等待上限压到 1 秒：用例只需证明「等待被记录」，不必真等 30 秒。"""
    monkeypatch.setattr(engine.pool, "_timeout", 1.0)
    return 1.0


@pytest.fixture
def exhausted_pool():
    """占满连接池，退出时保证归还（用例失败也不泄漏）。"""
    held = []
    try:
        for _ in range(POOL_MAX_CONNECTIONS):
            conn = engine.connect()
            conn.exec_driver_sql("SELECT 1")
            held.append(conn)
        yield held
    finally:
        for conn in held:
            conn.close()


def _records(caplog, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event]


def test_pool_wait_is_reported_when_the_pool_is_exhausted(
    db_session, caplog, exhausted_pool, short_pool_timeout
) -> None:
    """池满时 engine.connect() 的等待必须被记录，且时长≈池等待上限。

    记录必须发生在**异常路径**上：池满到超时正是最需要被观测的情形，
    而 `pool.connect()` 在这时会抛 TimeoutError。
    """
    caplog.set_level(logging.WARNING, logger="app.diagnostics")
    started = time.perf_counter()
    with pytest.raises(SATimeoutError):
        engine.connect()
    elapsed = time.perf_counter() - started

    events = _records(caplog, "db_pool_wait")
    assert events, "池满（且已超时）时未记录 db_pool_wait"
    record = events[-1]
    assert record.waited_ms >= config.DB_POOL_WAIT_WARN_MS
    assert record.waited_ms <= short_pool_timeout * 1000 + 500
    assert elapsed >= short_pool_timeout - 0.2
    assert record.checkedout >= POOL_MAX_CONNECTIONS


def test_no_pool_wait_record_when_connections_are_available(db_session, caplog) -> None:
    """池空闲时不得产生告警——否则告警会被淹没，等于没有诊断。"""
    caplog.set_level(logging.WARNING, logger="app.diagnostics")
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.close()
    assert not _records(caplog, "db_pool_wait")


def test_event_loop_lag_is_reported_when_the_loop_is_blocked(caplog, monkeypatch) -> None:
    """事件循环被同步代码占住时必须记录 event_loop_lag。"""
    monkeypatch.setattr(config, "EVENT_LOOP_LAG_WARN_MS", 50)
    monkeypatch.setattr(config, "EVENT_LOOP_LAG_INTERVAL_S", 0.05)
    caplog.set_level(logging.WARNING, logger="app.diagnostics")

    async def main() -> None:
        stop, task = runtime_diagnostics.start_event_loop_probe()
        # create_task 只是排队；先让出一次，探针才会真正进入等待。
        await asyncio.sleep(0)
        # 在事件循环上同步阻塞，模拟「同步 SQL 钉住循环」。
        time.sleep(0.3)
        # 再让出一次：探针需要一轮才能把这段阻塞计成 lag。若紧接着 set(stop)，
        # 定时器会与 stop 竞争，探针可能直接返回而错过本次阻塞。
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(main())
    events = _records(caplog, "event_loop_lag")
    assert events, "事件循环被阻塞时未记录 event_loop_lag"
    assert max(r.lag_ms for r in events) >= 50


def test_the_two_signals_are_distinguishable(
    db_session, caplog, monkeypatch, exhausted_pool, short_pool_timeout
) -> None:
    """区分性：仅池满（事件循环空闲）时只有 db_pool_wait，不得有 event_loop_lag。

    这是「阶段归属」的核心断言。若两者一起报，读者无法判断到底是「池被占满」
    （只影响需要连接的请求）还是「事件循环被占住」（拖垮所有端点），
    就会重演 09-30 的误判。
    """
    monkeypatch.setattr(config, "EVENT_LOOP_LAG_WARN_MS", 50)
    monkeypatch.setattr(config, "EVENT_LOOP_LAG_INTERVAL_S", 0.05)
    caplog.set_level(logging.WARNING, logger="app.diagnostics")

    async def main() -> None:
        stop, task = runtime_diagnostics.start_event_loop_probe()
        await asyncio.sleep(0)

        # 在工作线程里等连接：池满，但事件循环保持空闲。
        def wait_for_connection() -> None:
            try:
                engine.connect()
            except Exception:  # noqa: BLE001
                pass

        thread = threading.Thread(target=wait_for_connection)
        thread.start()
        while thread.is_alive():
            # 事件循环在此期间持续被调度（探针也因此在跑）。
            await asyncio.sleep(0.01)
        thread.join()
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(main())

    assert _records(caplog, "db_pool_wait"), "池满未被记录"
    assert not _records(caplog, "event_loop_lag"), (
        "池满但事件循环空闲时误报了 event_loop_lag；" "两个信号必须可区分，否则无法判断处置方向"
    )


def test_diagnostics_logs_carry_no_sensitive_fields(
    db_session, caplog, exhausted_pool, short_pool_timeout
) -> None:
    """诊断日志只含数值与安全标识，不得泄露 SQL/正文/凭据。"""
    caplog.set_level(logging.WARNING, logger="app.diagnostics")
    with pytest.raises(SATimeoutError):
        engine.connect()

    events = _records(caplog, "db_pool_wait")
    assert events
    baseline = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
    # `message` 是 LogRecord.getMessage() 计算后的字段，不是我们传的 extra。
    baseline |= {"message"}
    allowed = {"event", "waited_ms", "checkedout", "size", "overflow"}
    for record in events:
        extra_keys = set(record.__dict__) - baseline
        assert extra_keys <= allowed, f"诊断日志出现未预期字段: {extra_keys - allowed}"
        message = record.getMessage().upper()
        assert "SELECT" not in message
        assert "PRAGMA" not in message
