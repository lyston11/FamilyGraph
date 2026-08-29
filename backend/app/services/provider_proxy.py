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

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app import config
from app.errors import AGENT_PROVIDER_PROXY_UNAVAILABLE
from app.models.agent import AgentRun
from app.services import agent_provider, audit

logger = logging.getLogger(__name__)

#: 允许发起 Provider 调用的 Run 状态
_EXECUTABLE_RUN_STATUSES = ("leased", "running")

#: openai-completions 兼容路径（pi-ai/openai SDK 在 base_url 后追加）
_CHAT_COMPLETIONS_PATH = "/chat/completions"


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
    run: AgentRun,
    provider_id: int | None,
    status: str,
    status_code: int | None,
    bytes_read: int,
) -> None:
    audit.write_audit(
        db,
        action="agent_provider_egress",
        actor_id=None,
        target_id=run.id,
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


async def stream_provider_response(
    db: Session,
    *,
    run: AgentRun,
    space_id: int,
    body: bytes,
    content_type: str | None = None,
    accept: str | None = None,
    user_agent: str | None = None,
) -> tuple[Any, Any, int]:
    """向已注册 Provider 转发一次 chat/completions 请求，返回 (client, 上游流, provider_id)。

    调用方（端点）负责把流式响应透传回 sidecar；client 与流由
    passthrough_with_audit 在结束后统一关闭（避免连接泄漏）。
    任何解析/网络失败转换为 ProviderProxyError（脱敏，不携带上游 body）。
    """
    _require_executable_run(run)
    runtime = agent_provider.resolve_runtime(db, space_id)
    if runtime is None:
        # fail-closed：解析/解密失败一律可解释拒绝，绝不回退 sidecar env
        raise ProviderProxyError(
            503,
            AGENT_PROVIDER_PROXY_UNAVAILABLE,
            "Provider 当前不可用（未配置、被禁用或凭据失效）",
        )
    base_url = (runtime.base_url or "").rstrip("/")
    if not base_url:
        raise ProviderProxyError(503, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider base_url 未配置")
    target = f"{base_url}{_CHAT_COMPLETIONS_PATH}"
    headers = {"Authorization": f"Bearer {runtime.api_key}"} if runtime.api_key else {}
    # httpx 对原始字节 body 不自动设置 Content-Type；中转网关普遍强校验该头。
    # 透传 sidecar 原请求的三个无副作用头，其余（Cookie/凭据/追踪头）不转发。
    if content_type:
        headers["Content-Type"] = content_type
    if accept:
        headers["Accept"] = accept
    if user_agent:
        headers["User-Agent"] = user_agent
    try:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                float(config.AGENT_PROVIDER_PROXY_TIMEOUT_SECONDS),
                connect=float(config.AGENT_PROVIDER_PROXY_CONNECT_TIMEOUT_SECONDS),
            ),
        )
        upstream = await client.send(
            client.build_request("POST", target, content=body, headers=headers),
            stream=True,
        )
    except httpx.HTTPError:
        raise ProviderProxyError(
            502, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 暂时无法访问"
        ) from None
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
    bytes_read = 0
    try:
        async for chunk in upstream.aiter_raw():
            bytes_read += len(chunk)
            yield chunk
    finally:
        await upstream.aclose()
        await client.aclose()
        _audit_egress(
            db,
            run=run,
            provider_id=provider_id,
            status="succeeded",
            status_code=upstream.status_code,
            bytes_read=bytes_read,
        )
        on_finish()
