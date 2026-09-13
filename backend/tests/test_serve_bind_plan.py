"""bind 预检退避重试与 probe 语义（09-13-api-restart-bind-race）。

服务器实测：restart 后旧实例端口释放延迟（TIME_WAIT/优雅停机拖尾），预检
fail-fast 导致 systemd 5s 间隔循环重启约 1 分钟。这里锁定三个行为：进程内
有限退避重试、最终失败的可区分错误口径、probe 与 asyncio 真实绑定语义一致
（SO_REUSEADDR：TIME_WAIT 不算占用，活跃 LISTEN 仍拒绝）。
"""

from __future__ import annotations

import socket

import pytest

from app import serve


def _prober(fail_times: int, state: dict[str, int]):
    def _probe(host: str, port: int) -> None:
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise OSError(98, "Address already in use")

    return _probe


def test_bind_retry_succeeds_within_window() -> None:
    state = {"calls": 0}
    slept: list[float] = []
    serve._ensure_ports_bindable(
        [("internal", "127.0.0.1", 8001)],
        delays=(1.0, 2.0),
        sleep=slept.append,
        prober=_prober(2, state),
    )
    assert state["calls"] == 3
    assert slept == [1.0, 2.0]


def test_bind_retry_first_probe_success_skips_sleep() -> None:
    state = {"calls": 0}

    def _sleep(_: float) -> None:
        raise AssertionError("端口立即可绑定时不应退避")

    serve._ensure_ports_bindable(
        [("public", "127.0.0.1", 8000)],
        delays=(1.0,),
        sleep=_sleep,
        prober=_prober(0, state),
    )
    assert state["calls"] == 1


def test_bind_retry_exhaustion_raises_distinguishable_error() -> None:
    state = {"calls": 0}
    slept: list[float] = []
    with pytest.raises(RuntimeError) as excinfo:
        serve._ensure_ports_bindable(
            [("internal", "127.0.0.1", 8001)],
            delays=(0.5, 0.5),
            sleep=slept.append,
            prober=_prober(99, state),
        )
    message = str(excinfo.value)
    # 与「端口被他人占用」可区分：点明重试已穷尽、两种可能原因与定位命令。
    assert "重试 2 次后仍被占用" in message
    assert "lsof -iTCP:8001" in message
    assert slept == [0.5, 0.5]
    assert state["calls"] == 3


def test_probe_bind_sets_so_reuseaddr(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[object, ...]] = []
    binds: list[tuple[str, int]] = []

    class _StubSocket:
        def setsockopt(self, *args: object) -> None:
            recorded.append(args)

        def bind(self, address: tuple[str, int]) -> None:
            binds.append(address)

        def close(self) -> None:
            return None

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: _StubSocket())
    serve._probe_bind("127.0.0.1", 64444)
    assert recorded == [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
    assert binds == [("127.0.0.1", 64444)]


def test_probe_bind_rejects_active_listener() -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        with pytest.raises(OSError):
            serve._probe_bind("127.0.0.1", port)
    finally:
        blocker.close()


def test_probe_bind_allows_reused_port_without_listener() -> None:
    # TIME_WAIT 残留场景的代理验证：绑定后立即关闭（无 LISTEN），带 SO_REUSEADDR
    # 的 probe 必须能再次绑定——这正是旧实例刚退出、端口尚未完全清空的竞态现场。
    first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        first.bind(("127.0.0.1", 0))
        port = first.getsockname()[1]
    finally:
        first.close()
    serve._probe_bind("127.0.0.1", port)
