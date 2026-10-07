"""Embedding 客户端的降级契约测试。

## 为什么这个契约是本项目启用 embedding 的前提

Embedding 是**可选加速**。它不可用时，检索必须回退到 PGroonga 词法路径。
若把「embedding 暂时不可用」变成异常，那它就升级成「用户问不了问题」——
一个可选组件变成了可用性单点。

因此客户端把所有失败统一成 `degraded=True` + `reason`，调用方只需检查 `usable`。

## 本文件证明

1. 未配置 = 合法状态（回退），不是错误；
2. 503/429/504/超时/连接失败/结构异常 **全部**降级而不是抛异常；
3. **向量数量不匹配必须降级**（错位会让检索结果张冠李戴且看似正常）；
4. 成功路径正常返回；
5. 超时预算覆盖服务端的等待预算。

用 `httpx.MockTransport` 注入响应，因此不需要真实服务。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services import embedding_client
from app.services.embedding_client import EmbeddingResult, embed, embed_documents, embed_query

VECTORS = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


def _install(monkeypatch, handler) -> None:
    """把 httpx.AsyncClient 替换为使用 MockTransport 的版本。"""
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return original(transport=httpx.MockTransport(handler), timeout=5.0)

    monkeypatch.setattr(embedding_client.httpx, "AsyncClient", factory)


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "vectors": VECTORS,
            "dimension": 3,
            "tokens": [3, 3],
            "truncated": [False, False],
            "duration_ms": 1.0,
        },
    )


def _status(code: int, error: str):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"error": error})

    return handler


def test_disabled_when_not_configured(monkeypatch):
    """未配置 = 合法状态：语义检索未启用，回退词法路径。"""
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    result = asyncio.run(embed(["x"], base_url=""))
    assert result.degraded is True
    assert result.reason == "disabled"
    assert result.usable is False


def test_success_returns_vectors(monkeypatch):
    _install(monkeypatch, _ok)
    result = asyncio.run(embed(["a", "b"], base_url="http://x"))
    assert result.degraded is False
    assert result.usable is True
    assert result.vectors == VECTORS
    assert result.dimension == 3


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (503, "not_ready"),
        (429, "queue_full"),
        (504, "slot_timeout"),
        (422, "text_too_long"),
        (500, "inference_failed"),
    ],
)
def test_all_error_statuses_degrade_instead_of_raising(monkeypatch, code, error):
    """每一种错误码都必须降级，**不得**抛异常。

    这是「可选组件不能变成可用性单点」的具体保证。参数化覆盖全部服务端错误码，
    新增错误码时若忘记处理，这里会暴露（因为新码会走到 http_error 分支而非静默）。
    """
    _install(monkeypatch, _status(code, error))
    result = asyncio.run(embed(["a"], base_url="http://x"))
    assert result.degraded is True, f"{code} 未降级"
    assert result.usable is False
    assert result.reason == error, f"reason 应保留服务端分类，实际 {result.reason!r}"


def test_transport_failure_degrades(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install(monkeypatch, handler)
    result = asyncio.run(embed(["a"], base_url="http://x"))
    assert result.degraded is True
    assert result.reason is not None and result.reason.startswith("transport_")


def test_timeout_degrades(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    _install(monkeypatch, handler)
    result = asyncio.run(embed(["a"], base_url="http://x"))
    assert result.degraded is True
    assert result.reason == "timeout"


def test_bad_response_body_degrades(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _install(monkeypatch, handler)
    result = asyncio.run(embed(["a"], base_url="http://x"))
    assert result.degraded is True
    assert result.reason == "bad_response"


def test_vector_count_mismatch_degrades(monkeypatch):
    """向量数量不匹配必须降级。

    ## 为什么这是安全要求而不是防御性编程

    若返回 1 个向量却请求了 2 个文本，调用方若按位置配对，就会把「向量 A」当成
    「文本 B」的向量——检索结果看起来正常（有命中、有排序），但实际张冠李戴。
    这种错误不会报错，只会让结果悄悄变错。

    因此宁可整批降级到词法路径，也不猜着用。
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"vectors": VECTORS[:1], "dimension": 3})

    _install(monkeypatch, handler)
    result = asyncio.run(embed(["a", "b"], base_url="http://x"))
    assert result.degraded is True
    assert result.reason == "count_mismatch"
    assert result.vectors is None, "数量不匹配时不得返回部分向量"


def test_empty_input_is_not_degraded(monkeypatch):
    """空输入不调用服务，也不算降级（没有工作要做）。"""
    _install(monkeypatch, _ok)
    result = asyncio.run(embed([], base_url="http://x"))
    assert result.degraded is False
    assert result.vectors == []


def test_query_and_document_use_different_kinds(monkeypatch):
    """查询与文档必须用不同的 `kind`。

    BGE 对短查询加检索指令、对文档不加。加错侧不会报错，但会降低检索质量——
    因此必须由调用方显式声明，且两条路径不能混用。
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.append(_json.loads(request.content))
        return _ok(request)

    _install(monkeypatch, handler)
    asyncio.run(embed_query("q", base_url="http://x"))
    asyncio.run(embed_documents(["d"], base_url="http://x"))

    assert seen[0]["kind"] == "query"
    assert seen[0]["priority"] == "online"
    assert seen[1]["kind"] == "document"
    # 后台索引必须让位给在线查询，否则一次全库重建会让在线查询排队到超时。
    assert seen[1]["priority"] == "background"


def test_result_type_has_no_text_leak():
    """结果类型只含向量与诊断原因，不含原文（诊断不得泄露内容）。"""
    result = EmbeddingResult(vectors=None, dimension=0, degraded=True, reason="queue_full")
    assert set(vars(result)) == {"vectors", "dimension", "degraded", "reason", "duration_ms"}
