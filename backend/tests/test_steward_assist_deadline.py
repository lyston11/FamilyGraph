"""C：管家辅助请求的可中断总截止、结算预留与资源回收（09-17 steward-hard-deadline）。

复核反例（`main@d8d3668`）：旧实现只在 ``iter_bytes`` 返回一块数据之后检查
monotonic deadline，所以阻塞在等待响应头/等待下一块数据期间无法中断。本地
socket 服务（响应头延迟 300ms + 正文延迟 300ms）在 400ms 预算下实测约 638ms
才返回。本文件用同一类本地 socket 场景验证修复后的收敛，不访问真实 Provider。
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from test_steward import _run_job

from app import config
from app.models.steward import StewardAssistBatch, StewardModelCall
from app.services import steward_assist
from app.utils import timeutil

# ---- 本地 stall 服务：分别控制响应头延迟、正文延迟与分块节奏 ----


class StallServer:
    """最小 HTTP/1.1 chunked 服务：先延迟响应头，再延迟/持续滴答正文。"""

    def __init__(
        self,
        *,
        header_delay: float = 0.0,
        body_delay: float = 0.0,
        chunk_delay: float | None = None,
    ) -> None:
        self._header_delay = header_delay
        self._body_delay = body_delay
        self._chunk_delay = chunk_delay
        self._stop = threading.Event()
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.port = self._listener.getsockname()[1]
        self.connections: list[socket.socket] = []
        self.peer_closed = 0
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/responses"

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self.connections.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5.0)
            conn.recv(65536)
            if not self._wait(self._header_delay, conn):
                return
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            if not self._wait(self._body_delay, conn):
                return
            if self._chunk_delay is None:
                return
            payload = b'{"output": []}'
            while not self._stop.is_set():
                if not self._wait(self._chunk_delay, conn):
                    return
                conn.sendall(b"%x\r\n" % len(payload) + payload + b"\r\n")
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _wait(self, delay: float, conn: socket.socket) -> bool:
        """等待 ``delay`` 秒，但一旦客户端关闭连接就立即返回 False。

        返回 False 表示对端已关闭（客户端在总截止处真正取消了在途 I/O）；
        这是资源回收的可观察证据，而不是只靠一次 sleep 断言。
        """
        import select

        end = time.monotonic() + delay
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return True
            readable, _, _ = select.select([conn], [], [], min(0.02, remaining))
            if not readable:
                continue
            try:
                data = conn.recv(65536)
            except OSError:
                self.peer_closed += 1
                return False
            if data == b"":
                self.peer_closed += 1
                return False

    def wait_all_closed(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(conn.fileno() == -1 for conn in self.connections):
                return True
            time.sleep(0.02)
        return all(conn.fileno() == -1 for conn in self.connections)

    def close(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass


# ---- C-AC1：四类阻塞都在总预算内被中断 ----


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("header_stall", {"header_delay": 0.3, "body_delay": 0.3}),
        ("body_stall", {"header_delay": 0.0, "body_delay": 2.0}),
        ("slow_chunks", {"header_delay": 0.0, "body_delay": 0.0, "chunk_delay": 0.05}),
    ],
)
def test_stalled_reads_are_interrupted_within_the_total_budget(label, kwargs) -> None:
    """C-AC1：等待响应头、等待正文、持续慢 chunk 都必须在预算附近收敛。

    旧实现在 header_stall 场景要等数据到达后才检查截止，实测 400ms 预算约
    638ms 才返回；这里要求不超过预算 + 明确的调度容差。
    """
    server = StallServer(**kwargs)
    try:
        started = time.monotonic()
        with pytest.raises(httpx.ReadTimeout):
            steward_assist._post_json(server.url, {}, {"model": "m"}, 0.4)
        elapsed = time.monotonic() - started
    finally:
        server.close()

    # 0.4s 预算 + 0.35s 机器调度容差；关键是远低于旧实现的 ~0.638s 与
    # 场景自身的 2s 正文停顿。
    assert 0.35 <= elapsed < 0.75, f"{label}: elapsed={elapsed:.3f}s"
    assert server.wait_all_closed(), f"{label}: client connection not closed"


def test_connect_wait_is_bounded_by_the_total_budget() -> None:
    """连接/写入等待同样受总预算约束（不可路由地址不会拖满阶段超时）。"""
    started = time.monotonic()
    with pytest.raises((httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout)):
        steward_assist._post_json("http://10.255.255.1:9/v1/responses", {}, {"model": "m"}, 0.4)
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"connect wait={elapsed:.3f}s"


def test_repeated_deadlines_leave_no_lingering_connections() -> None:
    """C-AC3：连续超时后客户端必须真正关闭连接，不得累计活跃请求。"""
    server = StallServer(header_delay=0.0, body_delay=5.0)
    try:
        for _ in range(6):
            with pytest.raises(httpx.ReadTimeout):
                steward_assist._post_json(server.url, {}, {"model": "m"}, 0.2)
        assert len(server.connections) == 6
        # 每次超时都必须由客户端主动关闭（服务端观察到 EOF），而不是只让
        # 请求悬空、由服务端自己超时收尾。
        deadline = time.monotonic() + 2.0
        while server.peer_closed < 6 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.peer_closed == 6, f"peer_closed={server.peer_closed}"
        assert server.wait_all_closed(), "lingering client connections after repeated timeouts"
    finally:
        server.close()


def test_post_json_rejects_a_running_event_loop() -> None:
    """结构性误用 fail-closed：同步入口不得在事件循环内被调用。"""
    import asyncio

    async def call() -> None:
        with pytest.raises(RuntimeError):
            steward_assist._post_json("http://127.0.0.1:1/v1/responses", {}, {}, 0.1)

    asyncio.run(call())


# ---- C-R2：结算预留与出事务再核对 ----


def test_send_budget_reserves_settlement_time() -> None:
    """单笔预算 = min(配置 timeout, 剩余租约 - 结算预留)，且从不为负。"""
    lease_until = timeutil.utcnow() + timedelta(seconds=100)
    assert steward_assist._send_budget(lease_until) == pytest.approx(
        config.STEWARD_ASSIST_TIMEOUT_SECONDS
    )
    tight = timeutil.utcnow() + timedelta(seconds=5)
    assert steward_assist._send_budget(tight) == pytest.approx(
        5 - steward_assist._SETTLEMENT_RESERVE_SECONDS, abs=0.05
    )
    # 剩余不足以覆盖结算预留 → 预算 <= 0，调用方据此不发请求
    expired = timeutil.utcnow() + timedelta(seconds=1)
    assert steward_assist._send_budget(expired) <= 0
    assert steward_assist._send_budget(None) == 0.0


def test_transport_budget_excludes_the_settlement_reserve(db_session, monkeypatch) -> None:
    """C-AC2：实际传给 transport 的预算已扣除结算预留，不再吃满全部租约。"""
    space, _a, _b, event = _spouse_space_for_deadline(db_session)
    _turn_on_explanation_only(monkeypatch)
    monkeypatch.setattr(config, "STEWARD_ASSIST_BATCH_LEASE_SECONDS", 6)
    seen: list[float] = []

    def transport(url, headers, payload, timeout):
        seen.append(timeout)
        raise httpx.ReadTimeout("read timed out", request=httpx.Request("POST", url))

    _summary, job = _run_job(db_session, space, event.id)
    batch = _batch_for(db_session, job.id)
    assert steward_assist.schedule_due_batch(db_session) is not None
    steward_assist.execute_batch(db_session, batch.id, transport=transport)

    assert seen, "transport never entered"
    # 6s 租约 - 2s 结算预留 = 4s（配置 timeout 30s 不构成上界）
    assert all(t <= 4.0 + 0.05 for t in seen), seen
    assert any(t > 3.5 for t in seen), seen


def test_out_of_transaction_recheck_prevents_an_unsendable_request(db_session, monkeypatch) -> None:
    """C-AC2：事务提交后再核对一次——窗口不足则本笔从未发送（零计费、非 unknown）。"""
    space, _a, _b, event = _spouse_space_for_deadline(db_session)
    _turn_on_explanation_only(monkeypatch)
    calls: list[float] = []
    real_budget = steward_assist._send_budget
    seen_calls = {"n": 0}

    def budget(lease_until):
        seen_calls["n"] += 1
        # 第 1 次是出事务前粗筛，第 2 次是事务内、第 3 次是提交后。
        if seen_calls["n"] >= 3:
            return 0.0
        return real_budget(lease_until)

    monkeypatch.setattr(steward_assist, "_send_budget", budget)

    def transport(url, headers, payload, timeout):
        calls.append(timeout)
        return {}

    _summary, job = _run_job(db_session, space, event.id)
    batch = _batch_for(db_session, job.id)
    assert steward_assist.schedule_due_batch(db_session) is not None
    status = steward_assist.execute_batch(db_session, batch.id, transport=transport)

    assert calls == [], "unsendable request must not reach the transport"
    rows = _calls_for(db_session, job.id)
    assert rows and all(r.status == "skipped" for r in rows)
    assert all(r.error_code == steward_assist.REASON_INSUFFICIENT_BUDGET for r in rows)
    # skipped 不计费（也不进入预算状态集合）
    assert all(not r.billed_tokens for r in rows)
    assert status in ("failed", "applied", "superseded")


def test_released_reservations_do_not_consume_the_job_budget(db_session, monkeypatch) -> None:
    """C-R2/C-R4：未发送的释放为 skipped，不消耗调用/token 预算。"""
    space, _a, _b, event = _spouse_space_for_deadline(db_session)
    _turn_on_explanation_only(monkeypatch)
    monkeypatch.setattr(config, "STEWARD_ASSIST_BATCH_LEASE_SECONDS", 5)
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB", 1)
    calls: list[float] = []

    def transport(url, headers, payload, timeout):
        calls.append(timeout)
        raise httpx.ReadTimeout("read timed out", request=httpx.Request("POST", url))

    _summary, job = _run_job(db_session, space, event.id)
    batch = _batch_for(db_session, job.id)
    assert steward_assist.schedule_due_batch(db_session) is not None
    steward_assist.execute_batch(db_session, batch.id, transport=transport)

    assert len(calls) == 1, calls
    rows = _calls_for(db_session, job.id)
    assert len([r for r in rows if r.status == "skipped"]) >= 1
    assert steward_assist._budget_state(db_session, job.id)[0] == 1


# ---- 测试局部夹具（复用 test_steward_assist 的空间/开关构造）----


def _spouse_space_for_deadline(db_session):
    from test_steward_assist import _provider, _spouse_space, _steward_setting

    space, a, b, event = _spouse_space(db_session, "assist-hard-deadline")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, explanation=True)
    return space, a, b, event


def _turn_on_explanation_only(monkeypatch) -> None:
    from test_steward_assist import _turn_on

    _turn_on(monkeypatch, candidate=False, ranking=False)


def _batch_for(db_session, job_id: int) -> StewardAssistBatch:
    batch = db_session.scalar(select(StewardAssistBatch).where(StewardAssistBatch.job_id == job_id))
    assert batch is not None
    return batch


def _calls_for(db_session, job_id: int) -> list[StewardModelCall]:
    return list(
        db_session.scalars(
            select(StewardModelCall)
            .where(StewardModelCall.job_id == job_id)
            .order_by(StewardModelCall.id)
        )
    )
