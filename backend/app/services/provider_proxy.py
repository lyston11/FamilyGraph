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
from typing import Any, cast

import httpx
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app import config
from app.errors import AGENT_PROVIDER_PROXY_UNAVAILABLE, AGENT_PROVIDER_REQUEST_INVALID
from app.models.agent import AgentRun
from app.services import agent_provider, audit, policy_guard

logger = logging.getLogger(__name__)

#: 允许发起 Provider 调用的 Run 状态
_EXECUTABLE_RUN_STATUSES = ("leased", "running")

#: Pi OpenAI adapter paths appended to the configured Provider base URL.
_API_PATHS = {
    "openai-completions": "/chat/completions",
    "openai-responses": "/responses",
}
_TOKEN_CAP_FIELDS = ("max_tokens", "max_completion_tokens", "max_output_tokens")


class ProviderProxyError(Exception):
    """代理层错误（携带 API 错误码与状态，由端点转换为统一错误外壳）。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


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
) -> None:
    audit.write_audit(
        db,
        action="agent_provider_egress",
        actor_id=None,
        target_id=run if isinstance(run, int) else run.id,
        detail={
            "provider_id": provider_id,
            "status": status,
            "upstream_status": status_code,
            "bytes_read": bytes_read,
        },
    )


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


def _admit_upstream_request(db: Session, run_id: int) -> AgentRun:
    """Atomically admit one upstream request before opening the socket.

    ``SELECT`` followed by ``send`` leaves a cancellation TOCTOU window.  A
    no-op UPDATE acquires SQLite's write lock and compares the authoritative
    cancel/status fence in the same statement; cancellation either commits
    first (rowcount=0, request rejected) or waits until this admission is
    committed (the request is then considered already in flight).
    """
    db.rollback()
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
) -> tuple[Any, Any, int]:
    """向已注册 Provider 转发一次 chat/completions 请求，返回 (client, 上游流, provider_id)。

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
        _admit_upstream_request(db, run.id)
        upstream = await client.send(
            client.build_request("POST", target, content=body, headers=headers),
            stream=True,
        )
    except ProviderProxyError:
        if client is not None:
            await client.aclose()
        raise
    except httpx.HTTPError:
        if client is not None:
            await client.aclose()
        raise ProviderProxyError(
            502, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 暂时无法访问"
        ) from None
    assert client is not None  # construction either returned or raised above
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        # 上游错误体可能携带 secret/PII：只透出脱敏通用错误（redaction 合同）
        _audit_egress(
            db,
            run=run,
            provider_id=runtime.provider_id,
            status="failed",
            status_code=upstream.status_code,
            bytes_read=0,
        )
        raise ProviderProxyError(502, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 返回错误")
    return client, upstream, runtime.provider_id


async def passthrough_with_audit(
    db: Session,
    *,
    run: AgentRun,
    provider_id: int,
    client: Any,
    upstream: Any,
    on_finish: Any,
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
    try:
        async for chunk in upstream.aiter_raw():
            # Re-check between chunks.  If the browser cancels while a relay
            # is streaming, stop forwarding immediately and classify the
            # egress as failed; the sidecar cannot settle this run succeeded.
            _refresh_run_gate(db, run_id)
            bytes_read += len(chunk)
            yield chunk
    except (httpx.HTTPError, GeneratorExit, asyncio.CancelledError):
        outcome = "failed"
        raise
    except Exception:
        outcome = "failed"
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
        )
        on_finish()
