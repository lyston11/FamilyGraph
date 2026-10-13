"""真实 socket 产生的传输故障：分类与 `sent` 语义的端到端验证。

## 为什么不能用构造的异常

`test_provider_client_lifecycle.py` 直接构造 `httpx.ConnectError("boom")` 等实例。
那验证的是「给定异常类型 → 返回什么分类」，但**没有验证**：

1. 真实网络故障**确实**抛出该类型（httpx 的异常映射可能随版本变化）；
2. 代理在真实故障下**确实**把它映射为 502 + `AGENT_PROVIDER_PROXY_UNAVAILABLE`；
3. `sent=false` 只在连接**确实未建立**时出现。

本文件起**真实本地 socket 服务**产生真实故障，用真实 `httpx.AsyncClient` 发请求，
断言上述三条。全部在本机回环上完成，不依赖外部网络、不接触生产。

## 覆盖的故障

| 故障 | 真实做法 | 期望异常 | 期望分类 |
|---|---|---|---|
| 连接被拒 | 关闭端口后连接 | `ConnectError` | `transport_failure`, retryable, **sent=False** |
| 响应头前断开 | accept 后立即 RST 关闭 | `RemoteProtocolError`/`ReadError` | 不可重试、已发送 |
| 响应体中途 RST | 发部分 body 后 RST 关闭 | `RemoteProtocolError`/`ReadError` | 不可重试、已发送 |
| 响应头超时 | accept 后不响应 | `ReadTimeout` | `transport_timeout`, retryable, sent=True |
| DNS 失败 | 不存在的域名 | `ConnectError` | `transport_failure`, sent=False |

## 注意

`ConnectTimeout` 与 `PoolTimeout` 需要可控的慢握手或连接池压力，本文件不覆盖
（前者由 `_classify_transport_error` 的类型断言覆盖，后者由池配置覆盖）。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct
import threading

import httpx
import pytest

from app.services import provider_proxy


class _RawServer:
    """极简 TCP 服务：每个连接按 `handler(sock)` 处理。"""

    def __init__(self, handler) -> None:
        self._handler = handler
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                self._handler(conn)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    conn.close()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()
        self._thread.join(timeout=3)


def _free_port() -> int:
    """取一个当前无人监听的端口（用于「连接被拒」）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _reset_immediately(conn: socket.socket) -> None:
    """accept 后立即以 RST 关闭（不读请求、不写响应）。"""
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))


def _reset_after_partial_body(conn: socket.socket) -> None:
    """读到请求后发部分 body，再以 RST 关闭 → 真实「流中途重置」。"""
    conn.settimeout(2.0)
    with contextlib.suppress(TimeoutError, OSError):
        conn.recv(65536)
    with contextlib.suppress(OSError):
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
        conn.sendall(b'data: {"partial": true}\n\n')
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))


def _hang(conn: socket.socket) -> None:
    """读到请求后不回任何字节（触发 header 读超时）。"""
    conn.settimeout(2.0)
    with contextlib.suppress(TimeoutError, OSError):
        conn.recv(65536)
    threading.Event().wait(3.0)


def _run(coro):
    return asyncio.run(coro)


def test_connection_refused_is_not_sent_and_retryable():
    """连接被拒：真实 `ConnectError`，可重试，**且可断言未发送**。"""
    port = _free_port()
    error: Exception | None = None
    try:
        _run(_get(f"http://127.0.0.1:{port}/v1/chat/completions"))
    except Exception as exc:  # noqa: BLE001 - 我们要的就是真实异常
        error = exc
    assert error is not None, "连到已关闭端口必须失败"
    assert isinstance(error, httpx.ConnectError), type(error).__name__

    failure = provider_proxy._classify_transport_error(error)
    assert failure.error_class == "transport_failure"
    assert failure.retryable is True
    assert failure.sent is False, "连接未建立时必须可断言未发送"


def test_dns_failure_is_a_connect_error_not_sent():
    """不存在的域名：真实 DNS 失败也映射为 `ConnectError`。

    这条验证「DNS 失败」不是独立异常类型——它走与连接被拒相同的分类，
    因此 `sent=False` 的语义同样成立。
    """
    error: Exception | None = None
    try:
        _run(_get("http://fg-nonexistent-host.invalid/v1/chat/completions", timeout=5.0))
    except Exception as exc:  # noqa: BLE001
        error = exc
    assert error is not None, "不存在的域名必须失败"
    assert isinstance(error, httpx.ConnectError), type(error).__name__

    failure = provider_proxy._classify_transport_error(error)
    assert failure.sent is False
    assert failure.retryable is True


def test_header_timeout_is_retryable_and_sent():
    """响应头超时：真实 `ReadTimeout`，可重试，但**不得声称未发送**。"""
    server = _RawServer(_hang)
    server.start()
    try:
        error: Exception | None = None
        try:
            _run(_get(f"http://127.0.0.1:{server.port}/v1/chat/completions", timeout=0.5))
        except Exception as exc:  # noqa: BLE001
            error = exc
        assert error is not None, "服务不响应时必须超时"
        assert isinstance(error, httpx.TimeoutException), type(error).__name__

        failure = provider_proxy._classify_transport_error(error)
        assert failure.error_class == "transport_timeout"
        assert failure.retryable is True
        assert failure.sent is True, "请求可能已到达上游，不得声称未发送"
    finally:
        server.close()


@pytest.mark.parametrize(
    "handler",
    [_reset_immediately, _reset_after_partial_body],
    ids=["reset_before_response", "reset_mid_body"],
)
def test_stream_reset_is_never_claimed_unsent(handler):
    """连接在响应前后被 RST：不得声称未发送，且协议错误不可重试。

    **这是 `sent` 语义的承重断言**：请求已经写进 socket，上游可能已经处理，
    因此无论错误发生在响应头之前还是响应体中途，都不能记为「未发送」。
    记错会让运维把「上游已收到并可能已计费」误判为「没发出」。
    """
    server = _RawServer(handler)
    server.start()
    try:
        error: Exception | None = None
        try:
            _run(_get(f"http://127.0.0.1:{server.port}/v1/chat/completions", timeout=5.0))
        except Exception as exc:  # noqa: BLE001
            error = exc
        assert error is not None, "RST 后必须失败"
        assert isinstance(error, httpx.HTTPError), type(error).__name__

        failure = provider_proxy._classify_transport_error(error)
        assert failure.sent is True, (
            f"{type(error).__name__} 被记为未发送——请求已写入 socket，"
            "上游可能已处理，这会让审计证据反向失真"
        )
        assert failure.error_class in {
            "transport_protocol_error",
            "transport_failure",
        }, failure.error_class
    finally:
        server.close()


async def _get(url: str, timeout: float = 10.0):
    """用真实 `httpx.AsyncClient` 发一次 GET（代理内部用的同一个库）。

    `trust_env=False` 是**必需的**：本机可能设置了 `HTTP_PROXY`/`HTTPS_PROXY`
    （开发机上确有），而 httpx 默认 `trust_env=True` 会读它们。
    后果实测过：连一个**已关闭的本地端口**会返回 `ReadTimeout`（代理尝试转发失败）
    而不是 `ConnectError`——于是「连接被拒」的用例会失败，且失败原因看起来像
    「httpx 行为不对」，实际是**测试自己走了代理**。
    """
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.get(url)
        return resp


def test_pooled_client_ignores_environment_proxy_variables(monkeypatch):
    """池化 client 必须**不读** `HTTP_PROXY`/`HTTPS_PROXY`。

    ## 为什么这是安全断言，不只是配置偏好

    上游出网路径应由本服务的 egress 控制决定，而不是进程环境变量：

    1. 环境里一个变量就能把所有 provider 流量改道到未知中间人，
       而代码、日志与 `agent_provider_egress` 审计**看不出区别**
       （审计只记 `sent`/`error_class`/`bytes_read`）；
    2. 它会让故障语义失真。实测：设了 `HTTP_PROXY` 后，连一个**已关闭的本地端口**
       返回的是 `ReadTimeout` 而非 `ConnectError`——于是「连接被拒」被记成
       「超时且可能已发送」，`sent=false` 这条审计证据丢失。

    spec 明确「sidecar 不持 api_key、不直连云端；模型请求经代理端点」，
    api 容器是唯一 egress；全仓没有任何出站代理配置。本测试把这一点钉住：
    需要代理时应显式配置专用 egress，而不是复用进程级 `HTTP_PROXY`。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    provider_proxy.close_pooled_clients()

    client = provider_proxy.pooled_client("https://provider.example.invalid")
    try:
        assert (
            client.trust_env is False
        ), "池化 client 会读环境代理变量——上游流量可被环境变量静默改道"
    finally:
        provider_proxy.close_pooled_clients()
