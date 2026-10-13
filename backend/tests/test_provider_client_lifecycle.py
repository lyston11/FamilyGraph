"""Provider 连接生命周期（持久 client，P3-c）。

## 这组测试防的是哪一类缺陷

把「每请求新建 `AsyncClient`」改成「按上游分池复用」是一个**看起来只是性能优化**
的改动，但它有三个真实的失效模式，且都不会自己报错：

1. **池被关闭**：某个 `except` 分支仍 `await client.aclose()`，于是下一个请求拿到
   一个已关闭的 client 直接失败——而且是间歇性的（取决于哪个分支先跑）。
2. **跨上游复用**：用单一全局 client，连接被错误复用到别的 provider 主机上。
3. **凭据泄漏到池**：把 `Authorization` 设成 client 的默认头，于是**所有**上游
   请求都带同一个凭据。

因此这里逐条锁定，而不是只断言「复用更快」。
"""

from __future__ import annotations

import httpx
import pytest

from app.services import provider_proxy


@pytest.fixture(autouse=True)
def _clean_pool():
    """每个测试前后清空池：池是模块级状态，测试之间必须隔离。"""
    provider_proxy.close_pooled_clients()
    yield
    provider_proxy.close_pooled_clients()


def test_same_base_url_reuses_one_client():
    """同一上游必须复用同一 client——这正是 50.5ms/请求 的来源。"""
    first = provider_proxy.pooled_client("https://a.example/v1")
    second = provider_proxy.pooled_client("https://a.example/v1")
    assert first is second


def test_different_base_urls_get_different_clients():
    """不同上游必须隔离：跨上游复用连接会让连接被错误复用到别的主机。"""
    left = provider_proxy.pooled_client("https://a.example/v1")
    right = provider_proxy.pooled_client("https://b.example/v1")
    assert left is not right
    assert left is provider_proxy.pooled_client("https://a.example/v1")


def test_pool_is_bounded():
    """池必须有上界：`base_url` 被频繁改动时不得无界增长。"""
    clients = [
        provider_proxy.pooled_client(f"https://host{index}.example/v1")
        for index in range(provider_proxy._MAX_POOLED_CLIENTS + 3)
    ]
    assert len(provider_proxy._POOLED_CLIENTS) <= provider_proxy._MAX_POOLED_CLIENTS
    # 最新的那些必须在池里（淘汰的是最旧的）。
    assert clients[-1] is provider_proxy.pooled_client(
        f"https://host{provider_proxy._MAX_POOLED_CLIENTS + 2}.example/v1"
    )


def test_close_pooled_clients_clears_everything():
    provider_proxy.pooled_client("https://a.example/v1")
    provider_proxy.close_pooled_clients()
    assert provider_proxy._POOLED_CLIENTS == {}


def test_credentials_are_not_baked_into_the_pooled_client():
    """凭据必须是**每请求**的，不得成为 client 默认头。

    否则一个上游的凭据会随池复用泄漏给另一个上游。
    """
    client = provider_proxy.pooled_client("https://a.example/v1")
    assert "authorization" not in {key.lower() for key in client.headers}


def test_no_request_path_closes_the_pooled_client():
    """静态断言：请求路径里不得出现 `client.aclose()`。

    关闭池化 client 会让连接池失效（收益归零），且下一个请求会拿到已关闭的
    client 而间歇性失败。这个缺陷不会自己报错，因此必须静态锁定。
    """
    source = __import__("pathlib").Path(provider_proxy.__file__).read_text(encoding="utf-8")
    assert (
        "await client.aclose()" not in source
    ), "请求路径不得关闭池化 client；关闭会让连接池失效并使后续请求失败"


def test_sent_determinism_does_not_depend_on_connection_novelty():
    """`sent` 的判定只看异常类型，与连接是否新建无关。

    池化后握手失败仍抛同样的异常类型，因此 `sent=false` 的语义不变。
    """
    assert provider_proxy._classify_transport_error(httpx.ConnectError("boom")).sent is False
    assert provider_proxy._classify_transport_error(httpx.ConnectTimeout("boom")).sent is False
    assert provider_proxy._classify_transport_error(httpx.ReadTimeout("boom")).sent is True


def test_proxy_request_path_actually_uses_the_pool(internal_client, db_session, monkeypatch):
    """接线测试：端点必须**真的**用池化 client，而不是新建一个。

    ## 为什么必须测接线而不是只测 `pooled_client`

    只测 `pooled_client` 本身的话，把端点改回「每请求新建」**不会让任何测试失败**
    ——实测确认过（mutation：把 `client = pooled_client(base_url)` 换回
    `httpx.AsyncClient(...)`，7 项测试全绿）。那意味着这组测试没有守住这次改动的
    实际收益。

    做法：patch `httpx.AsyncClient` 为一个计数器，跑一次真实的代理请求，断言
    新建次数为 0（因为池已经提供 client）。
    """
    from test_provider_proxy import (  # noqa: PLC0415 - 复用既有的假上游与造数
        _FakeAsyncClient,
        _install_fake,
        _lease_run_token,
        _seed_provider,
    )

    from app.services import agent_queue
    from conftest import create_agent_session

    _install_fake(monkeypatch, [b'{"ok": true}'])
    created: list[str] = []
    real_async_client = _FakeAsyncClient

    def counting_async_client(*args, **kwargs):
        created.append("new")
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", counting_async_client)

    user, space, provider = _seed_provider(db_session, name="pooled-wiring")
    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()

    # 预热池：模拟「同一上游的第二个请求」——这是池化真正生效的场景。
    provider_proxy.pooled_client(provider.base_url)
    created.clear()

    run_id, token = _lease_run_token(internal_client)
    response = internal_client.post(
        f"/internal/agent/runs/{run.id}/provider/chat/completions",
        content=b'{"model": "model-x", "messages": [], "stream": true}',
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert created == [], (
        f"请求路径新建了 {len(created)} 个 AsyncClient；池化未生效，" "50ms/请求的收益没有兑现"
    )
    del run_id


def test_transport_error_classification_covers_every_production_branch():
    """`_classify_transport_error` 的**每一个**分支都必须有断言。

    ## 为什么需要这条

    生产实现按异常类型分派 6 条路径（ConnectTimeout / ConnectError /
    TimeoutException / RemoteProtocolError / TransportError / 非传输 HTTPError），
    但测试只注入了 `ConnectError` 与 `ReadError` 两类，且 `sent` 断言只覆盖
    3 种异常。

    未被断言的后果是**安全语义可以被静默改坏**：例如把 `RemoteProtocolError`
    从 `retryable=False` 改成 `True`，或在 `TransportError` 分支把
    `sent=True` 改成 `False`，都不会让任何测试失败——而 `sent=false` 是
    egress 审计里「本次出站未发送」的唯一证据，改错会让运维据此误判上游影响面。

    这里逐个分支钉住 `(error_class, retryable, sent)` 三元组。
    """
    cases = [
        # (异常, error_class, retryable, sent)
        # 连接**未建立** → 可断言未发送。
        (httpx.ConnectTimeout("t"), "transport_timeout", True, False),
        (httpx.ConnectError("c"), "transport_failure", True, False),
        # 请求可能已到达上游 → 只能标为已发送。
        (httpx.ReadTimeout("r"), "transport_timeout", True, True),
        (httpx.WriteTimeout("w"), "transport_timeout", True, True),
        (httpx.PoolTimeout("p"), "transport_timeout", True, True),
        # 协议中断：上游已收到请求，但**不可重试**（重试可能重复副作用）。
        (httpx.RemoteProtocolError("p"), "transport_protocol_error", False, True),
        # 其他传输错误：已发送且不可重试。
        (httpx.ReadError("e"), "transport_failure", False, True),
        # 非传输类 HTTPError（如构造请求失败）：发送确定性**未知**，不得当作可重试。
        (httpx.InvalidURL("u"), "transport_failure", False, None),
    ]
    for error, expected_class, expected_retryable, expected_sent in cases:
        failure = provider_proxy._classify_transport_error(error)
        assert failure.error_class == expected_class, (type(error).__name__, failure.error_class)
        assert failure.retryable is expected_retryable, (
            type(error).__name__,
            failure.retryable,
        )
        assert failure.sent is expected_sent, (type(error).__name__, failure.sent)


def test_only_connection_establishment_failures_claim_not_sent():
    """`sent=False` 只允许由「连接未建立」的证据得出。

    `sent=false` 在 egress 审计里是「本次出站未发送」的唯一证据，运维据此判断
    上游是否受影响。放宽它（例如把读超时也标为未发送）会让上游明明收到了请求
    却被记成「没发出」——**审计证据反向失真**。
    """
    not_sent = [
        httpx.ConnectTimeout("t"),
        httpx.ConnectError("c"),
    ]
    for error in not_sent:
        assert provider_proxy._classify_transport_error(error).sent is False, type(error).__name__

    # 这些都可能发生在请求已到达上游之后 → 不得声称未发送。
    must_be_sent = [
        httpx.ReadTimeout("r"),
        httpx.WriteTimeout("w"),
        httpx.PoolTimeout("p"),
        httpx.RemoteProtocolError("p"),
        httpx.ReadError("e"),
        httpx.WriteError("e"),
    ]
    for error in must_be_sent:
        assert provider_proxy._classify_transport_error(error).sent is True, type(error).__name__
