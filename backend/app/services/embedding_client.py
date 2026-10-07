"""Embedding 服务的客户端（backend 侧）。

## 核心契约：失败必须**回退**，不能变成检索失败

Embedding 是**可选加速**。它不可用时，检索必须回退到 PGroonga 词法路径，
而不是报错——否则「embedding 挂了」会升级成「用户问不了问题」。

因此本客户端**不抛异常**（除编程错误），而是返回一个明确的结果类型：

```python
EmbeddingResult(vectors=..., degraded=True, reason="queue_full")
EmbeddingResult(vectors=None, degraded=True, reason="unavailable")
```

调用方据此决定「只用词法」还是「词法 + 向量」。

## 超时预算

客户端超时必须**小于**服务端的 `EMBEDDING_WAIT_TIMEOUT_SECONDS + 推理时间`，
否则调用方会在服务端还在正常处理时放弃。这里默认 8 秒（服务端等待 5 秒 +
推理余量）。

## 后台索引与在线查询使用不同优先级

`priority="background"` 让后台索引在服务端排队时让位给在线查询。没有这个区分，
一次全库重建会让所有在线查询排队到超时。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

# `httpx` 在模块层导入而不是函数内：
#
# 1. 测试需要注入 `httpx.MockTransport`（函数内导入让 `embedding_client.httpx`
#    不存在，无法替换）；
# 2. httpx 已是本项目依赖（`provider_proxy` 使用），模块层导入不增加成本。
import httpx

logger = logging.getLogger(__name__)

#: 服务地址。未配置 = 语义检索**未启用**（合法状态，回退词法路径）。
DEFAULT_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "")

#: 客户端超时。必须覆盖服务端的等待预算（默认 5s）加推理时间。
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("EMBEDDING_REQUEST_TIMEOUT_SECONDS", "8"))


@dataclass(frozen=True)
class EmbeddingResult:
    """编码结果。

    `degraded=True` 表示**必须回退词法路径**。`reason` 用于诊断与审计，
    不包含文本内容。
    """

    vectors: list[list[float]] | None
    dimension: int
    degraded: bool
    reason: str | None = None
    duration_ms: float | None = None

    @property
    def usable(self) -> bool:
        return not self.degraded and bool(self.vectors)


def enabled() -> bool:
    """语义检索是否已配置。

    未配置是**合法状态**：部署可能只用词法检索。因此返回 False 而不是抛错。
    """
    return bool(DEFAULT_BASE_URL)


async def embed(
    texts: list[str],
    *,
    kind: str = "document",
    priority: str = "online",
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> EmbeddingResult:
    """调用 embedding 服务。

    ## 为什么所有失败都归为 degraded

    调用方只有两种合理动作：用向量，或不用向量。把 503/429/504/连接失败/超时
    区分成不同异常，会让每个调用点都要写一遍相同的 try/except——而漏掉任何一个
    分支就会让「embedding 暂时不可用」变成「检索失败」。

    因此这里统一成 `degraded=True` + `reason`，调用方只需检查 `usable`。

    ## 422 也归为 degraded

    422 表示请求非法（文本过长/数量超限）。重试无用，但**也不该让整个检索失败**
    ——正确的行为是记录并只用词法路径。日志里保留原因供修正上界。
    """
    import time

    url = (base_url if base_url is not None else DEFAULT_BASE_URL).rstrip("/")
    if not url:
        return EmbeddingResult(vectors=None, dimension=0, degraded=True, reason="disabled")
    if not texts:
        return EmbeddingResult(vectors=[], dimension=0, degraded=False)

    payload = {"texts": texts, "kind": kind, "priority": priority}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds or REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{url}/embed", json=payload)
    except httpx.TimeoutException:
        # 超时：服务端可能仍在推理（名额会在推理结束后才释放，见 embedding/app/gate.py）。
        # 这里放弃等待，但服务端会自行收敛。
        logger.warning("embedding 请求超时 kind=%s priority=%s n=%d", kind, priority, len(texts))
        return EmbeddingResult(vectors=None, dimension=0, degraded=True, reason="timeout")
    except httpx.HTTPError as exc:
        logger.warning("embedding 请求失败 error_class=%s", type(exc).__name__)
        return EmbeddingResult(
            vectors=None, dimension=0, degraded=True, reason=f"transport_{type(exc).__name__}"
        )

    duration_ms = (time.perf_counter() - started) * 1000.0
    if response.status_code != 200:
        # 服务端返回的是**分类后的**错误码（not_ready/queue_full/slot_timeout/…），
        # 直接用作 reason，便于运维区分「队列满」与「模型没加载」。
        reason = "http_error"
        try:
            reason = str(response.json().get("error", reason))
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "embedding 降级 status=%d reason=%s n=%d", response.status_code, reason, len(texts)
        )
        return EmbeddingResult(
            vectors=None, dimension=0, degraded=True, reason=reason, duration_ms=duration_ms
        )

    try:
        body = response.json()
        vectors = body["vectors"]
        dimension = int(body["dimension"])
    except Exception:  # noqa: BLE001
        logger.warning("embedding 响应结构异常")
        return EmbeddingResult(
            vectors=None,
            dimension=0,
            degraded=True,
            reason="bad_response",
            duration_ms=duration_ms,
        )

    if len(vectors) != len(texts):
        # 数量不匹配是**服务端缺陷**，不能猜着用：错位会让向量与文本对应错误，
        # 检索结果看似正常但实际张冠李戴。
        logger.error("embedding 返回 %d 个向量但请求 %d 个文本", len(vectors), len(texts))
        return EmbeddingResult(
            vectors=None,
            dimension=dimension,
            degraded=True,
            reason="count_mismatch",
            duration_ms=duration_ms,
        )

    return EmbeddingResult(
        vectors=vectors, dimension=dimension, degraded=False, duration_ms=duration_ms
    )


async def embed_query(text: str, *, base_url: str | None = None) -> EmbeddingResult:
    """编码**查询**（在线优先级）。

    `kind="query"` 使服务端加上 BGE 检索指令——对短查询加指令是官方建议，
    加错侧不会报错但会降低检索质量。
    """
    return await embed([text], kind="query", priority="online", base_url=base_url)


async def embed_documents(texts: list[str], *, base_url: str | None = None) -> EmbeddingResult:
    """编码**文档**（后台优先级）。

    后台索引是**可延迟**工作，因此用 background 优先级让位给在线查询。
    """
    return await embed(texts, kind="document", priority="background", base_url=base_url)
