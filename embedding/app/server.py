"""Embedding HTTP 服务。

## 端点

| 端点 | 用途 |
|---|---|
| `GET /healthz` | 存活（进程在跑）——不检查模型 |
| `GET /readyz` | 就绪（模型已加载）+ 预算摘要 + 闸门指标 |
| `POST /embed` | 编码文本 |

`/healthz` 与 `/readyz` 分离是必要的：模型加载要数秒，若存活探针也检查模型，
容器会在启动期被判定不健康并反复重启，而每次重启都要重新加载模型——形成
「永远起不来」的循环。

## 错误语义（调用方据此决定回退）

| 状态 | 场景 | 调用方应做 |
|---|---|---|
| 503 `not_ready` | 模型未加载完成 | **回退词法检索**，稍后重试 |
| 429 `queue_full` | 等待队列已满（背压） | 回退；后台索引跳过本轮 |
| 504 `slot_timeout` | 等待名额超时 | 回退 |
| 422 | 请求非法（超长/超数量） | **不重试**（重试仍非法） |

503/429/504 都是「暂时不可用」→ 回退；422 是「请求有问题」→ 修正。

## 为什么返回 `truncated`

模型上限 512 tokens。超长文本会被**静默截断**——调用方会以为整段都进了向量，
实际只有前半段。因此响应显式报告每个文本的 token 数与是否被截断，让调用方
能决定「切更细再编码」而不是无声丢失内容。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import EmbeddingConfig, from_env
from app.gate import (
    PRIORITY_BACKGROUND,
    PRIORITY_ONLINE,
    InferenceGate,
    QueueFull,
    SlotTimeout,
)
from app.model import ModelNotReady, OnnxEncoder

logger = logging.getLogger("embedding")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_state: dict[str, Any] = {}


class EmbedRequest(BaseModel):
    """编码请求。`extra='forbid'` 使拼错的字段被拒绝而不是静默忽略。"""

    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(min_length=1)
    # 显式声明是查询还是文档：BGE 对两者处理不同（查询加指令，文档不加）。
    # 猜错不会报错，只会降低检索质量，因此必须由调用方声明。
    kind: str = Field(default="document", pattern="^(query|document)$")
    # 后台索引使用 background 优先级，避免全库重建把在线查询挤到超时。
    priority: str = Field(default="online", pattern="^(online|background)$")


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    dimension: int
    tokens: list[int]
    truncated: list[bool]
    duration_ms: float


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = from_env()
    _state["config"] = config
    _state["gate"] = InferenceGate(
        max_concurrent=config.max_concurrent,
        max_waiting=config.max_waiting,
        wait_timeout_seconds=config.wait_timeout_seconds,
    )
    encoder = OnnxEncoder(
        model_path=config.model_path,
        tokenizer_path=config.tokenizer_path,
        max_tokens=config.max_tokens,
        threads=config.threads,
        dimension=config.dimension,
        query_instruction=config.query_instruction,
    )
    _state["encoder"] = encoder
    _state["started_at"] = time.time()
    # 加载放在后台线程：先让 HTTP 起来（healthz 立刻可用），模型在数秒内就绪。
    # 不阻塞事件循环——加载期间的 /healthz 必须能响应，否则容器被误判为死掉。
    asyncio.get_running_loop().run_in_executor(None, encoder.load)
    logger.info(
        "embedding 服务启动 model=%s dim=%d max_concurrent=%d max_waiting=%d "
        "threads=%d",
        config.model_name,
        config.dimension,
        config.max_concurrent,
        config.max_waiting,
        config.threads,
    )
    yield
    _state.clear()


app = FastAPI(title="familygraph-embedding", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    """存活探针：只证明进程在跑。"""
    return {"status": "alive", "uptime_seconds": round(time.time() - _state["started_at"], 1)}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """就绪探针 + 预算摘要 + 闸门指标（只含计数器，无文本）。"""
    encoder: OnnxEncoder = _state["encoder"]
    gate: InferenceGate = _state["gate"]
    config: EmbeddingConfig = _state["config"]
    ready = encoder.ready
    body = {
        "ready": ready,
        "model": encoder.status(),
        "budget": config.public_summary(),
        "gate": gate.metrics().summary(),
    }
    return JSONResponse(body, status_code=200 if ready else 503)


@app.post("/embed")
async def embed(payload: EmbedRequest) -> JSONResponse:
    config: EmbeddingConfig = _state["config"]
    gate: InferenceGate = _state["gate"]
    encoder: OnnxEncoder = _state["encoder"]

    if not encoder.ready:
        # 503 而非 500：这是「暂时不可用」，调用方应回退词法检索。
        return JSONResponse(
            {"error": "not_ready", "detail": encoder.load_error}, status_code=503
        )

    # 请求上界：单个请求不得独占名额。超限是**请求问题**（422），不是暂时故障。
    if len(payload.texts) > config.max_texts_per_request:
        return JSONResponse(
            {
                "error": "too_many_texts",
                "limit": config.max_texts_per_request,
                "actual": len(payload.texts),
            },
            status_code=422,
        )
    oversized = [i for i, t in enumerate(payload.texts) if len(t) > config.max_chars_per_text]
    if oversized:
        return JSONResponse(
            {
                "error": "text_too_long",
                "limit": config.max_chars_per_text,
                "indices": oversized[:10],
            },
            status_code=422,
        )

    priority = (
        PRIORITY_ONLINE if payload.priority == "online" else PRIORITY_BACKGROUND
    )
    is_query = payload.kind == "query"
    started = time.perf_counter()

    def _run() -> tuple[list[list[float]], list[int], list[bool]]:
        # 关键：取得名额、推理、释放**都在同一个工作线程内**。
        # 因此 `wait_timeout` 只约束等待名额，不约束推理本身——推理一旦开始就跑完，
        # 名额在结束后才释放。这保证「并发上界」对实际在跑的推理成立，而不是只对
        # 已返回的请求成立。
        with gate.slot(priority=priority):
            return encoder.encode(payload.texts, is_query=is_query)

    try:
        vectors, tokens, truncated = await asyncio.to_thread(_run)
    except QueueFull as exc:
        return JSONResponse(
            {"error": "queue_full", "detail": str(exc)}, status_code=429
        )
    except SlotTimeout as exc:
        return JSONResponse(
            {"error": "slot_timeout", "detail": str(exc)}, status_code=504
        )
    except ModelNotReady as exc:
        return JSONResponse(
            {"error": "not_ready", "detail": str(exc)}, status_code=503
        )
    except Exception:  # noqa: BLE001
        # 不把内部细节（路径、栈）返回给调用方；日志保留完整信息供排查。
        logger.exception("embedding 推理失败")
        return JSONResponse({"error": "inference_failed"}, status_code=500)

    return JSONResponse(
        EmbedResponse(
            vectors=vectors,
            dimension=config.dimension,
            tokens=tokens,
            truncated=truncated,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
        ).model_dump()
    )
@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """兜底：不向调用方泄露堆栈，但完整记录到日志。"""
    logger.exception("embedding 未处理异常 path=%s", request.url.path)
    return JSONResponse({"error": "internal_error"}, status_code=500)
