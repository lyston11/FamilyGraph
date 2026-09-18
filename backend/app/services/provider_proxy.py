"""ProviderGateway 代理（P1 唯一 egress 收口的执行层）。

此前仅"解密集中"：真实模型请求仍由 sidecar 携带下发凭据直连云端 Provider。
本模块把模型流量收口到 api 容器：sidecar 以 run token 调 internal 代理端点，
代理在服务端重新解密凭据并转发到已注册 Provider；sidecar 不再持有 api_key、
不再需要外网 egress（compose backend 网络置 internal 后强制成立）。

fail-closed 合同：
- run token 与 run_id 双向核验（复用 internal 协议 _authorize_run 语义）；
- Run 必须处于可执行状态（leased/running）；
- Provider 解析失败/凭据解密失败一律 503 可解释拒绝，绝不回退 sidecar env；
- 上游错误只透出脱敏后的通用错误体，上游 body/secret 不进响应、日志与事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    AGENT_PROVIDER_PROXY_UNAVAILABLE,
    AGENT_PROVIDER_REQUEST_INVALID,
    AGENT_PROVIDER_UPSTREAM_REJECTED,
)
from app.models.agent import AgentRun
from app.services import agent_provider, audit, policy_guard
from app.services.agent_execution import ExecutionIdentity, fence_execution

logger = logging.getLogger(__name__)

#: 允许发起 Provider 调用的 Run 状态
_EXECUTABLE_RUN_STATUSES = ("leased", "running")

#: Pi OpenAI adapter paths appended to the configured Provider base URL.
_API_PATHS = {
    "openai-completions": "/chat/completions",
    "openai-responses": "/responses",
}
_TOKEN_CAP_FIELDS = ("max_tokens", "max_completion_tokens", "max_output_tokens")

#: 4xx 中确实可重试的状态码：408 请求超时、409 冲突（上游可证暂时）、
#: 425 Too Early、429 限流。其余 4xx 都是「请求本身不可接受」（凭据/参数/模型/路由），
#: 重发不会成功。5xx 仍按暂时性处理（设计表：408/429/5xx 可重试）。
_UPSTREAM_RETRYABLE_4XX = frozenset({408, 409, 425, 429})

#: 让 pi-ai 的请求层重试立即停止（SDK 的 isRetryableProviderError 优先读该头）。
_NO_RETRY_HEADERS = {"x-should-retry": "false"}

#: 暂时错误的**有界退避**提示（毫秒）。pi-ai 的 `getRetryDelayMs` 优先读 `retry-after-ms`，
#: 否则退回 `min(0.5·2^i, 8)s`：当前预算（5 次）的最坏退避是 0.5+1+2+4+8 = 15.5s，
#: 在 3s 首段目标下不可接受。实测线上 5 次快速 502 本身只需约 4.5s，却因退避累加把整轮拉到 33s。
#: 取 500ms = 默认退避的**第一级**：对任意重试次数都不劣于默认（1 次重试同值），
#: 且 5 次重试从 15.5s 降到 2.5s。不取更大值是因为那会让少次数重试反而变慢。
#: **次数不变**：可用性证据显示 5 次重试确实会被用满（2 个 run / 11 次出站 / 7 次失败，
#: 最终都靠重试成功），降次数会让这类轮次直接失败。
_TRANSIENT_RETRY_HEADERS = {"retry-after-ms": "500"}


@dataclass(frozen=True)
class EgressFailure:
    """一次出站尝试的安全分类结果。

    ``sent`` 是发送确定性：True=已发送（上游可能已处理）、False=未建立连接
    （未发送）、None=不可判定。它只允许由传输层证据得出，不用来声称上游未处理。
    """

    error_class: str
    retryable: bool
    sent: bool | None


class ProviderProxyError(Exception):
    """代理层错误（携带 API 错误码与状态，由端点转换为统一错误外壳）。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        # 透给 sidecar 的安全重试提示（例如永久错误携带 x-should-retry:false）。
        self.headers = headers


def provider_proxy_base_url(run_id: int) -> str:
    """context 下发给 sidecar 的代理 base_url（站内绝对路径）。

    sidecar 将其 resolve 到 internal listener（FG_INTERNAL_API_BASE_URL），
    并以 run token 作为 Bearer 凭据调用；真实 Provider 凭据不出服务端。
    """
    return f"/internal/agent/runs/{run_id}/provider"


def _audit_egress(
    db: Session,
    *,
    run: AgentRun | int,
    provider_id: int | None,
    status: str,
    status_code: int | None,
    bytes_read: int,
    error_class: str | None = None,
    retryable: bool | None = None,
    sent: bool | None = None,
    header_ms: int | None = None,
) -> None:
    """每次真实出站尝试的唯一安全终态。

    ``status`` 保持既有取值（succeeded/failed/blocked_by_policy），D 的聚合消费
    不受影响；``error_class``/``retryable`` 是新增的**安全分类**（机器码/布尔，
    无上游原文、无 prompt、无凭据），供重试治理与审计对账。
    ``header_ms`` 是 gateway-side 等待**响应头**的耗时：这是判断上游首响应期限
    是否合理的唯一依据（sidecar 的 `first_text_ms` 是正文增量时间，不可代替）。
    """
    detail: dict[str, object] = {
        "provider_id": provider_id,
        "status": status,
        "upstream_status": status_code,
        "bytes_read": bytes_read,
    }
    if error_class is not None:
        detail["error_class"] = error_class
    if retryable is not None:
        detail["retryable"] = retryable
    if sent is not None:
        detail["sent"] = sent
    if header_ms is not None:
        detail["header_ms"] = header_ms
    audit.write_audit(
        db,
        action="agent_provider_egress",
        actor_id=None,
        target_id=run if isinstance(run, int) else run.id,
        detail=detail,
    )


def _classify_transport_error(error: httpx.HTTPError) -> EgressFailure:
    """把**建立连接/读取响应头**阶段的异常映射为安全分类与发送确定性。

    连接未建立（ConnectError/ConnectTimeout）可断言**未发送**；
    ``ReadTimeout``/``WriteTimeout``/``ReadError``/``RemoteProtocolError`` 在
    ``send(stream=True)`` 返回前也可能发生在上游已收到请求之后，只能标为已发送，
    不得声称“未被处理”。（已开始响应后的中断走 ``stream_interrupted``。）
    """
    if isinstance(error, httpx.ConnectTimeout):
        return EgressFailure("transport_timeout", retryable=True, sent=False)
    if isinstance(error, httpx.ConnectError):
        return EgressFailure("transport_failure", retryable=True, sent=False)
    if isinstance(error, httpx.TimeoutException):
        return EgressFailure("transport_timeout", retryable=True, sent=True)
    if isinstance(error, httpx.RemoteProtocolError):
        return EgressFailure("transport_protocol_error", retryable=False, sent=True)
    if isinstance(error, httpx.TransportError):
        return EgressFailure("transport_failure", retryable=False, sent=True)
    # 非传输类 HTTPError（如构造请求失败）：发送确定性未知，不当作可重试。
    return EgressFailure("transport_failure", retryable=False, sent=None)


def _classify_upstream_status(status_code: int) -> EgressFailure:
    """上游状态码 → 安全分类：永久拒绝不得伪装为可重试上游 5xx。"""
    if 400 <= status_code < 500 and status_code not in _UPSTREAM_RETRYABLE_4XX:
        return EgressFailure("upstream_rejected", retryable=False, sent=True)
    return EgressFailure("upstream_transient", retryable=True, sent=True)


def _await_response_headers(
    send_coro: Any,
    *,
    header_timeout_seconds: float,
) -> Any:
    """等待上游响应头，并施加**仅针对这一阶段**的期限，返回 (response, header_ms)。

    连接超时与总超时都拦不住「连接已建立、但上游迟迟不返回响应头」：实测线上单次
    503 拖了 29.8s（bytes_read=0）才返回。这里给响应头阶段一个独立期限，同时用
    ``wait_for`` 在超时时**取消**等待（httpx 的 read timeout 只中断等待，不取消）。

    响应头之后的流式生成不在本期限范围内：长回答可以继续流式输出，不会被误杀。
    返回的 ``header_ms`` 是该阶段的实测耗时，写入安全审计供期限调参。
    """

    async def _timed() -> Any:
        started = time.monotonic()
        response = await send_coro
        return response, int((time.monotonic() - started) * 1000)

    if header_timeout_seconds <= 0:
        return _timed()
    return asyncio.wait_for(_timed(), timeout=header_timeout_seconds)


def _require_executable_run(run: AgentRun) -> None:
    if run.status not in _EXECUTABLE_RUN_STATUSES:
        raise ProviderProxyError(409, "AGENT_RUN_NOT_RUNNING", "Provider 调用仅在 Run 活跃期间允许")
    if run.cancel_requested:
        # Cancellation is server-authoritative.  A sidecar heartbeat may race
        # with a new provider request, so the gateway must reject it even while
        # the FSM still says leased/running.
        raise ProviderProxyError(409, "AGENT_RUN_NOT_RUNNING", "Run 已请求取消")


def _refresh_run_gate(db: Session, run: AgentRun | int) -> AgentRun:
    """Refresh status/cancel flag before opening or continuing upstream I/O.

    The cancellation endpoint commits on another request.  Roll back any
    read-only transaction left by the auth/projection queries first so SQLite
    starts a fresh snapshot and cannot keep serving a stale cancel flag.
    """
    run_id = run if isinstance(run, int) else run.id
    db.rollback()
    fresh = db.get(AgentRun, run_id)
    if fresh is None:
        raise ProviderProxyError(409, "AGENT_RUN_NOT_RUNNING", "Run 不存在")
    _require_executable_run(fresh)
    return fresh


def _admit_upstream_request(
    db: Session, run_id: int, *, execution: ExecutionIdentity | None = None
) -> AgentRun:
    """Atomically admit one upstream request before opening the socket.

    ``SELECT`` followed by ``send`` leaves a cancellation TOCTOU window.  A
    no-op UPDATE acquires SQLite's write lock and compares the authoritative
    cancel/status fence in the same statement; cancellation either commits
    first (rowcount=0, request rejected) or waits until this admission is
    committed (the request is then considered already in flight).
    """
    db.rollback()
    if execution is not None:
        try:
            run, _session, _job = fence_execution(db, execution)
        except HTTPException as exc:
            db.rollback()
            from app.errors import extract_api_error

            error = extract_api_error(exc.detail) or {}
            raise ProviderProxyError(
                exc.status_code,
                str(error.get("code", "AGENT_TOKEN_SCOPE_MISMATCH")),
                "执行身份已失效",
            ) from None
        db.commit()
        return run
    result = cast(
        CursorResult[Any],
        db.execute(
            text(
                "UPDATE agent_runs SET updated_at = updated_at "
                "WHERE id = :run_id AND status IN ('leased','running') "
                "AND cancel_requested = 0"
            ),
            {"run_id": run_id},
        ),
    )
    if result.rowcount != 1:
        db.rollback()
        raise ProviderProxyError(409, "AGENT_RUN_NOT_RUNNING", "Run 已停止或请求取消")
    db.commit()
    fresh = db.get(AgentRun, run_id)
    if fresh is None:  # pragma: no cover - row was matched by UPDATE
        raise ProviderProxyError(409, "AGENT_RUN_NOT_RUNNING", "Run 不存在")
    return fresh


def _validate_runtime_payload(
    payload: dict[str, Any], runtime: agent_provider.ProviderRuntime
) -> None:
    """Bind the wire body to the immutable run model/capability snapshot.

    The sidecar is trusted only as a token holder; a compromised worker must
    not repoint a valid run at another allowlisted model or exceed its pinned
    output cap.  Reject before constructing an upstream request.
    """
    if payload.get("model") != runtime.model:
        raise ProviderProxyError(
            422, AGENT_PROVIDER_REQUEST_INVALID, "Provider model 与 Run 配置不匹配"
        )
    if payload.get("stream") is not True:
        raise ProviderProxyError(
            422, AGENT_PROVIDER_REQUEST_INVALID, "Provider 请求必须启用流式响应"
        )
    for field in _TOKEN_CAP_FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProviderProxyError(
                422, AGENT_PROVIDER_REQUEST_INVALID, "Provider token 上限格式无效"
            )
        if value > runtime.max_tokens:
            raise ProviderProxyError(
                422, AGENT_PROVIDER_REQUEST_INVALID, "Provider token 上限超过 Run 配置"
            )


async def stream_provider_response(
    db: Session,
    *,
    run: AgentRun,
    space_id: int,
    body: bytes,
    content_type: str | None = None,
    accept: str | None = None,
    user_agent: str | None = None,
    expected_api: str | None = None,
    execution: ExecutionIdentity | None = None,
) -> tuple[Any, Any, int, int | None]:
    """向已注册 Provider 转发一次 chat/completions 请求。

    返回 ``(client, 上游流, provider_id, header_ms)``。header_ms 是 gateway-side 等待
    响应头的实测耗时，写入安全审计供首响应期限调参。

    调用方（端点）负责把流式响应透传回 sidecar；client 与流由
    passthrough_with_audit 在结束后统一关闭（避免连接泄漏）。
    任何解析/网络失败转换为 ProviderProxyError（脱敏，不携带上游 body）。
    """
    run = _refresh_run_gate(db, run)
    if not body:
        # An empty request has no valid OpenAI payload and must not become an
        # anonymous/side-effectful upstream POST. Reject before resolving or
        # decrypting provider credentials.
        raise ProviderProxyError(422, AGENT_PROVIDER_REQUEST_INVALID, "Provider 请求体不能为空")
    runtime = agent_provider.resolve_runtime(db, space_id, run=run)
    if runtime is None:
        # fail-closed：解析/解密失败一律可解释拒绝，绝不回退 sidecar env
        raise ProviderProxyError(
            503,
            AGENT_PROVIDER_PROXY_UNAVAILABLE,
            "Provider 当前不可用（未配置、被禁用或凭据失效）",
        )
    if expected_api is not None and runtime.api != expected_api:
        # The route is part of the protocol contract.  Do not let a caller
        # invoke /responses for a chat-completions snapshot (or vice versa)
        # and rely on a downstream 4xx to discover the mismatch.
        raise ProviderProxyError(
            422,
            AGENT_PROVIDER_REQUEST_INVALID,
            "Provider 路由与 Run 配置的协议不匹配",
        )
    base_url = (runtime.base_url or "").rstrip("/")
    if not base_url:
        raise ProviderProxyError(503, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider base_url 未配置")
    if body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderProxyError(
                422, AGENT_PROVIDER_REQUEST_INVALID, "Provider 请求格式无效"
            ) from None
        if not isinstance(payload, dict):
            raise ProviderProxyError(
                422, AGENT_PROVIDER_REQUEST_INVALID, "Provider 请求必须是 JSON 对象"
            )
        _validate_runtime_payload(payload, runtime)
        resolution = agent_provider.resolve_for_run(db, run, space_id)
        decision = policy_guard.before_provider_request(
            payload,
            provider_kind=runtime.kind,
            cloud_allowed=resolution.policy_result == agent_provider.POLICY_ALLOWED,
        )
        if decision.action == "block":
            _audit_egress(
                db,
                run=run,
                provider_id=runtime.provider_id,
                status="blocked_by_policy",
                status_code=None,
                bytes_read=0,
                error_class="blocked_by_policy",
                retryable=False,
                # 发送前拒绝：零上游请求，不得冒充已发送的上游尝试。
                sent=False,
            )
            raise ProviderProxyError(409, "POLICY_PROVIDER_BLOCKED", "策略阻止了本次 Provider 请求")
        if decision.action == "redact":
            outbound = decision.value if decision.value is not None else payload
            body = json.dumps(outbound, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    api_path = _API_PATHS.get(runtime.api)
    if api_path is None:
        raise ProviderProxyError(503, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 协议不受支持")
    target = f"{base_url}{api_path}"
    headers = {"Authorization": f"Bearer {runtime.api_key}"} if runtime.api_key else {}
    # httpx 对原始字节 body 不自动设置 Content-Type；中转网关普遍强校验该头。
    # 透传 sidecar 原请求的三个无副作用头，其余（Cookie/凭据/追踪头）不转发。
    if content_type:
        headers["Content-Type"] = content_type
    if accept:
        headers["Accept"] = accept
    if user_agent:
        headers["User-Agent"] = user_agent
    client: httpx.AsyncClient | None = None
    try:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                float(config.AGENT_PROVIDER_PROXY_TIMEOUT_SECONDS),
                connect=float(config.AGENT_PROVIDER_PROXY_CONNECT_TIMEOUT_SECONDS),
            ),
        )
        # Atomically admit the request immediately before constructing the
        # upstream POST.  A later cancellation may stop the stream, but cannot
        # retroactively revoke an already-admitted request.
        _admit_upstream_request(db, run.id, execution=execution)
        upstream, header_ms = await _await_response_headers(
            client.send(
                client.build_request("POST", target, content=body, headers=headers),
                stream=True,
            ),
            header_timeout_seconds=float(config.AGENT_PROVIDER_PROXY_HEADER_TIMEOUT_SECONDS),
        )
    except ProviderProxyError:
        if client is not None:
            await client.aclose()
        raise
    except TimeoutError:
        if client is not None:
            await client.aclose()
        # 首响应期限到期：请求已发出（连接已建立），上游可能已处理，
        # 不得声称「未发送」；可重试交给请求层预算决定。
        _audit_egress(
            db,
            run=run,
            provider_id=runtime.provider_id,
            status="failed",
            status_code=None,
            bytes_read=0,
            error_class="transport_timeout",
            retryable=True,
            sent=True,
            header_ms=int(float(config.AGENT_PROVIDER_PROXY_HEADER_TIMEOUT_SECONDS) * 1000),
        )
        raise ProviderProxyError(
            502, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 暂时无法访问"
        ) from None
    except httpx.HTTPError as exc:
        if client is not None:
            await client.aclose()
        # 连接异常同样必须留安全终态（E-R2）；分类不读异常原文。
        failure = _classify_transport_error(exc)
        _audit_egress(
            db,
            run=run,
            provider_id=runtime.provider_id,
            status="failed",
            status_code=None,
            bytes_read=0,
            error_class=failure.error_class,
            retryable=failure.retryable,
            sent=failure.sent,
        )
        raise ProviderProxyError(
            502, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 暂时无法访问"
        ) from None
    assert client is not None  # construction either returned or raised above
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        # 上游错误体可能携带 secret/PII：只透出脱敏通用错误（redaction 合同）
        failure = _classify_upstream_status(upstream.status_code)
        _audit_egress(
            db,
            run=run,
            provider_id=runtime.provider_id,
            status="failed",
            status_code=upstream.status_code,
            bytes_read=0,
            error_class=failure.error_class,
            retryable=failure.retryable,
            sent=failure.sent,
            header_ms=header_ms,
        )
        if not failure.retryable:
            # 永久错误：不得伪装为可重试上游 5xx（否则 sidecar 会重试）。
            # 保留真实上游状态供审计，响应体仍是脱敏通用错误。
            raise ProviderProxyError(
                upstream.status_code,
                AGENT_PROVIDER_UPSTREAM_REJECTED,
                "Provider 拒绝了本次请求",
                headers=dict(_NO_RETRY_HEADERS),
            )
        # 暂时错误：保留既有可重试形状，但把请求层退避压到有界值。
        # 429 是上游自己的限流信号，换成一秒提示会变成每秒捶打上游；
        # 上游已给 Retry-After 时尊重上游，否则给短退避。
        transient_headers: dict[str, str] = {}
        if upstream.status_code != 429 and not (
            upstream.headers.get("retry-after") or upstream.headers.get("retry-after-ms")
        ):
            transient_headers = dict(_TRANSIENT_RETRY_HEADERS)
        raise ProviderProxyError(
            502,
            AGENT_PROVIDER_PROXY_UNAVAILABLE,
            "Provider 返回错误",
            headers=transient_headers,
        )
    return client, upstream, runtime.provider_id, header_ms


async def passthrough_with_audit(
    db: Session,
    *,
    run: AgentRun,
    provider_id: int,
    client: Any,
    upstream: Any,
    on_finish: Any,
    header_ms: int | None = None,
) -> Any:
    """流式透传生成器：逐块回传 sidecar，结束后统计字节数并落用量审计。

    客户端中断/生成器关闭时同样关闭上游流（不泄漏连接）；审计提交由
    on_finish（端点注入的 db.commit）负责，错误不回滚已透传内容。
    """
    # Keep a scalar id: the request-scoped SQLAlchemy session may expire or
    # detach the ORM instance between streaming chunks.
    run_id = run.id
    bytes_read = 0
    outcome = "succeeded"
    failure: EgressFailure | None = None
    try:
        async for chunk in upstream.aiter_raw():
            # Re-check between chunks.  If the browser cancels while a relay
            # is streaming, stop forwarding immediately and classify the
            # egress as failed; the sidecar cannot settle this run succeeded.
            _refresh_run_gate(db, run_id)
            bytes_read += len(chunk)
            yield chunk
    except ProviderProxyError:
        # 流中复核失败：取消/失租是服务端权威裁决（请求已发出），
        # 不能标成上游错误或“未处理”。
        outcome = "failed"
        failure = EgressFailure("run_cancelled", retryable=False, sent=True)
        raise
    except httpx.HTTPError:
        # 流中中断/超时：上游可能已处理（部分响应已发出），
        # 标为 stream_interrupted 且 sent=True，不得声称“未被处理”。
        outcome = "failed"
        failure = EgressFailure("stream_interrupted", retryable=False, sent=True)
        raise
    except (GeneratorExit, asyncio.CancelledError):
        outcome = "failed"
        failure = EgressFailure("stream_interrupted", retryable=False, sent=True)
        raise
    except Exception:
        outcome = "failed"
        failure = EgressFailure("stream_interrupted", retryable=False, sent=True)
        raise
    finally:
        await upstream.aclose()
        await client.aclose()
        _audit_egress(
            db,
            run=run_id,
            provider_id=provider_id,
            status=outcome,
            status_code=upstream.status_code,
            bytes_read=bytes_read,
            error_class=failure.error_class if failure is not None else None,
            retryable=failure.retryable if failure is not None else None,
            sent=failure.sent if failure is not None else None,
            header_ms=header_ms,
        )
        on_finish()
