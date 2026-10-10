"""Internal Agent 协议端点（design.md；挂载前缀 /internal/agent，无 /api）。

认证两级（notes.md 裁定）：
- POST /jobs/lease 仅收 sidecar service token；
- 其余 run 级端点仅收 lease 响应签发的 run token，且与 DB 实体双向核验
  （claims.run_id/job_id、account/space/kind/allowlist 不一致一律 fail-closed）。
用户 JWT 打到本路由一律 403（先于 token 解析识别），并写安全审计。

路由形态说明：design.md 的 `events:append` 与 `runs/{id}:settle` 冒号写法在部分
HTTP 客户端/代理中易产生路径转义歧义，这里用普通段 `/events/append` 与 `/settle`
实现，语义一致（任务说明允许二选一）。

成功写入的端点显式提交；拒绝路径遵循 auth 惯例「先提交审计再抛错」。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import weakref
from typing import Any, NoReturn

import anyio
from fastapi import APIRouter, Depends, Request, Response
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db
from app.db import SessionLocal
from app.errors import (
    AGENT_CONTEXT_INVALIDATED,
    AGENT_DISABLED,
    AGENT_EVENT_INVALID,
    AGENT_EXECUTION_BUSY,
    AGENT_INTERNAL_FORBIDDEN,
    AGENT_JOB_NOT_FOUND,
    AGENT_PROVIDER_PROXY_UNAVAILABLE,
    AGENT_RUN_NOT_FOUND,
    AGENT_RUN_NOT_RUNNING,
    AGENT_TOKEN_INVALID,
    AGENT_TOKEN_SCOPE_MISMATCH,
    STEWARD_DISABLED,
    extract_api_error,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import AgentJob, AgentMessage, AgentRun, AgentSession
from app.models.space import SpaceMember
from app.models.steward import StewardModelCall
from app.schemas.agent import (
    ContextMessageOut,
    ContextOut,
    ContextProviderOut,
    EventAcceptedOut,
    EventAppendOut,
    EventAppendRequest,
    HeartbeatOut,
    HeartbeatRequest,
    LeaseOut,
    LeaseRequest,
    SettleOut,
    SettleRequest,
    StewardLeaseOut,
    StewardLeaseRequest,
    ToolExecuteOut,
    ToolExecuteRequest,
)
from app.services import (
    agent_events,
    agent_provider,
    agent_queue,
    agent_tokens,
    agent_tools,
    audit,
    capacity,
    context_builder,
    policy_guard,
    redis_accel,
    steward_assist,
)
from app.services.agent_admission import AdmissionRejected, ResourceLimiter
from app.services.agent_events import EventEntry
from app.services.agent_execution import (
    ExecutionIdentity,
    StewardExecution,
    fence_assistant_execution,
    fence_steward_execution,
)
from app.services.provider_proxy import provider_proxy_base_url as agent_provider_proxy_base_url
from app.utils import security, timeutil

logger = logging.getLogger(__name__)


def _require_agent_enabled() -> None:
    """RT-6：Agent 能力由服务端 feature flag 总开关控制，默认整体关闭。"""
    if not config.AGENT_RUNTIME_ENABLED:
        raise_api_error(503, AGENT_DISABLED, "Agent Runtime 未启用")


router = APIRouter(
    tags=["agent-internal"],
    dependencies=[Depends(_require_agent_enabled)],
)


def _bearer_token(request: Request) -> str | None:
    scheme, _, raw = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not raw:
        return None
    return raw


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _deny(
    db: Session,
    request: Request,
    *,
    reason: str,
    status_code: int,
    code: str,
    message: str,
    detail: dict[str, object] | None = None,
) -> NoReturn:
    """安全审计先行提交，再抛统一错误（auth.py 拒绝路径同款惯例）。"""
    audit.write_audit(
        db,
        action="agent_internal_authz_denied",
        actor_id=None,
        target_id=None,
        ip=_client_ip(request),
        detail={"reason": reason},
    )
    db.commit()
    raise_api_error(status_code, code, message, detail)


def _audit_and_raise(
    db: Session,
    *,
    action: str,
    target_id: int | None,
    detail: dict[str, object],
    status_code: int,
    code: str,
    message: str,
    api_detail: dict[str, object] | None = None,
) -> None:
    audit.write_audit(db, action=action, actor_id=None, target_id=target_id, detail=detail)
    db.commit()
    raise_api_error(status_code, code, message, api_detail)


def _reject_user_jwt(db: Session, request: Request) -> None:
    """携带有效用户 JWT 的请求打 internal 一律 403（无效 JWT 交给后续 token 校验）。"""
    raw = _bearer_token(request)
    if raw is None:
        return
    try:
        security.decode_token(raw, security.ACCESS_TOKEN_TYPE)
    except security.TokenDecodeError:
        return
    _deny(
        db,
        request,
        reason="user_jwt_on_internal",
        status_code=403,
        code=AGENT_INTERNAL_FORBIDDEN,
        message="内部协议不接受用户凭据",
    )


def _decode_or_deny(db: Session, request: Request, *, typ: str) -> dict[str, Any]:
    raw = _bearer_token(request)
    if raw is None:
        _deny(
            db,
            request,
            reason="token_missing",
            status_code=401,
            code=AGENT_TOKEN_INVALID,
            message="缺少内部凭据",
        )
    try:
        if typ == agent_tokens.SERVICE_TOKEN_TYPE:
            return agent_tokens.decode_service_token(raw)
        return agent_tokens.decode_run_token(raw)
    except agent_tokens.AgentTokenError:
        _deny(
            db,
            request,
            reason=f"{typ}_invalid",
            status_code=401,
            code=AGENT_TOKEN_INVALID,
            message="内部凭据无效或已过期",
        )


def _authorize_assistant_run(
    db: Session, request: Request, run_id: int
) -> tuple[AgentRun, AgentSession, dict[str, Any]]:
    """run token 解码 + 与 DB 实体双向核验（scope 五元组 + allowlist）。

    仅服务 assistant：Steward child run 无 session、无 queue job，判据本质不同
    （见 ``_authorize_steward_run``）。用一个函数按 kind 分支会让两侧的检查集
    互相污染，因此拆开并在入口断言 kind。
    """
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["agent_kind"] != "assistant":
        _deny(
            db,
            request,
            reason="kind_mismatch",
            status_code=403,
            code=AGENT_TOKEN_SCOPE_MISMATCH,
            message="token 不是 assistant run token",
        )
    if claims["run_id"] != run_id:
        _deny(
            db,
            request,
            reason="run_id_mismatch",
            status_code=403,
            code=AGENT_TOKEN_SCOPE_MISMATCH,
            message="token 与目标 Run 不匹配",
        )
    run = db.get(AgentRun, run_id)
    if run is None or run.kind != "assistant":
        # Kind is checked before the session is touched: a steward run has no
        # session, so reading it first would turn a scope violation into an
        # assertion failure rather than a clean denial. (The steward authorizer
        # makes the mirror-image check.)
        raise_api_error(404, AGENT_RUN_NOT_FOUND, "Run 不存在")
    agent_session = db.get(AgentSession, run.session_id)
    assert agent_session is not None
    job = db.get(AgentJob, run.job_id) if run.job_id is not None else None
    if (
        claims["account_id"] != agent_session.account_id
        or claims["space_id"] != agent_session.space_id
        # Lease-time attempt binding: a token issued for an earlier lease of
        # this run can never be replayed against the current execution.
        or claims["attempt"] != run.attempt
        or claims["agent_kind"] != run.kind
        or claims["job_id"] != run.job_id
        or sorted(claims["tool_allowlist"]) != sorted(run.tool_allowlist_json or [])
        or job is None
        or job.attempt != claims["attempt"]
        or job.run_id != run.id
        or job.account_id != agent_session.account_id
        or job.space_id != agent_session.space_id
        or job.kind != run.kind
    ):
        _deny(
            db,
            request,
            reason="scope_mismatch_vs_db",
            status_code=403,
            code=AGENT_TOKEN_SCOPE_MISMATCH,
            message="token scope 与执行实体不一致",
        )
    # Membership is deliberately re-evaluated for every internal request;
    # revoking a user invalidates an already-issued run token immediately.
    account = db.get(Account, agent_session.account_id)
    member = db.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == agent_session.space_id,
            SpaceMember.user_id == (account.user_id if account is not None else -1),
            SpaceMember.status == "active",
        )
    )
    # owner_id 不是运行时授权来源（PRD R2/R6）：授权只看目标空间的 active
    # membership。迁移 0022 已把历史 owner 落成 active space_admin 成员行，
    # 因此这里不再保留 owner_id fallback。
    if member is None:
        _deny(
            db,
            request,
            reason="active_membership_missing",
            status_code=403,
            code=AGENT_TOKEN_SCOPE_MISMATCH,
            message="Run 所属空间成员资格已失效",
        )
    return run, agent_session, claims


def _authorize_steward_run(
    db: Session, request: Request, run_id: int
) -> tuple[AgentRun, dict[str, Any]]:
    """steward child run 的 token 核验（空间级，无账号）。

    只做「token 指向的就是这个 run」的前置拒绝；完整层级判定（父 job 活跃、空间
    匹配、viewer 仍为 active 成员、batch 绑定、租约）交给 ``fence_steward_execution``
    ——它在写锁内重验，且与 ``fence_assistant_execution`` 拥有独立检查集。
    """
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["agent_kind"] != "steward":
        _deny(
            db,
            request,
            reason="kind_mismatch",
            status_code=403,
            code=AGENT_TOKEN_SCOPE_MISMATCH,
            message="token 不是 steward run token",
        )
    if claims["run_id"] != run_id:
        _deny(
            db,
            request,
            reason="run_id_mismatch",
            status_code=403,
            code=AGENT_TOKEN_SCOPE_MISMATCH,
            message="token 与目标 Run 不匹配",
        )
    run = db.get(AgentRun, run_id)
    if run is None or run.kind != "steward":
        raise_api_error(404, AGENT_RUN_NOT_FOUND, "Run 不存在")
    return run, claims


def _authorize_run(
    db: Session, request: Request, run_id: int
) -> tuple[AgentRun, AgentSession, dict[str, Any]]:
    """Assistant-path alias: most internal endpoints are assistant-only.

    Kept so those call sites read unchanged; the kind assertion inside makes a
    steward token unable to reach them.
    """
    return _authorize_assistant_run(db, request, run_id)


def _authorize_provider_run(
    db: Session, request: Request, run_id: int
) -> tuple[AgentRun, int, dict[str, Any]]:
    """Authorize one gateway call from either kind, returning its space.

    The gateway is the single egress, so both kinds must reach it: a steward child
    run that is refused here has no way to make a model call at all, and its egress
    would go unaudited. The token's kind decides which authorizer runs — the two
    keep independent, complete check sets — and this function only resolves the
    space each one anchors on (an assistant run's session, a steward run's claim).
    """
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["agent_kind"] == "steward":
        run, _steward_claims = _authorize_steward_run(db, request, run_id)
        return run, int(claims["space_id"]), claims
    run, agent_session, _claims = _authorize_assistant_run(db, request, run_id)
    return run, agent_session.space_id, _claims


def _require_active_run(db: Session, request: Request, run: AgentRun) -> None:
    """Reject post-lease protocol writes once a Run has stopped being active."""
    if run.status not in ("queued", "leased", "running"):
        _deny(
            db,
            request,
            reason="run_not_active",
            status_code=409,
            code=AGENT_RUN_NOT_RUNNING,
            message="Run 不在活跃状态",
            detail={"status": run.status},
        )


# ---- jobs ----


@router.post("/jobs/lease", response_model=LeaseOut | None)
def lease_job(
    body: LeaseRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LeaseOut | Response:
    """sidecar 以 service token 租赁一个 queued job；无可租返回 204。"""
    _reject_user_jwt(db, request)
    _decode_or_deny(db, request, typ=agent_tokens.SERVICE_TOKEN_TYPE)
    # The HTTP lease endpoint is exclusively for the Assistant sidecar.  The
    # canonical Steward worker runs in the API maintenance loop and leases
    # directly through the deterministic service; accepting another kind or an
    # omitted kind here would let a service-token caller consume another queue.
    if body.kind != "assistant":
        _deny(
            db,
            request,
            reason="non_assistant_lease_rejected",
            status_code=403,
            code=AGENT_INTERNAL_FORBIDDEN,
            message="Steward 作业仅可由系统维护 worker 执行",
        )
    grant = agent_queue.lease_next(
        db,
        kind=body.kind,
        leased_by=body.leased_by,
        ttl_seconds=body.lease_ttl_seconds,
    )
    if grant is None:
        return Response(status_code=204)
    run_token = agent_tokens.issue_run_token(
        run_id=grant.run.id,
        job_id=grant.job.id,
        attempt=grant.job.attempt,
        agent_kind=grant.run.kind,
        account_id=grant.job.account_id or 0,
        space_id=grant.job.space_id or 0,
        tool_allowlist=list(grant.run.tool_allowlist_json),
    )
    return LeaseOut(
        job_id=grant.job.id,
        run_id=grant.run.id,
        agent_kind=grant.run.kind,
        attempt=grant.job.attempt,
        tool_allowlist=list(grant.run.tool_allowlist_json),
        policy_version=grant.run.policy_version,
        run_token=run_token,
    )


@router.post("/steward/attempts/lease", response_model=StewardLeaseOut | None)
def lease_steward_attempt(
    body: StewardLeaseRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> StewardLeaseOut | Response:
    """Steward sidecar 租一个 attempt 及其 child run；无可租返回 204。

    独立于 ``/jobs/lease`` 而不是放开后者的 kind：两个端点服务两个不同的队列，
    独立路由让「哪个容器能租哪类作业」成为**路由级**约束而不是 payload 校验——
    否则任何持有 service token 的调用者（含被入侵的 assistant 容器）都能消费
    Steward 队列。

    路径从 ``/steward/jobs/lease`` 改为 ``/steward/attempts/lease``：执行单元是
    attempt，不是 job；名字必须与实际租的东西一致，否则调用方会以为自己在租一个
    作业级单位。
    """
    _reject_user_jwt(db, request)
    _decode_or_deny(db, request, typ=agent_tokens.SERVICE_TOKEN_TYPE)
    # 双开关：引擎开启（STEWARD_ENABLED）与执行载体切换
    # （STEWARD_PI_RUNTIME_ENABLED）是两个独立的发布决策，必须能各自回退。
    if not (config.STEWARD_ENABLED and config.STEWARD_PI_RUNTIME_ENABLED):
        raise_api_error(503, STEWARD_DISABLED, "Steward Pi runtime 未开启")
    from app.models.steward import CARRIER_PI

    grant = steward_assist.lease_attempt(
        db,
        space_id=body.space_id,
        worker_id=body.leased_by,
        carrier=CARRIER_PI,
        ttl_seconds=body.lease_ttl_seconds,
    )
    if grant is None:
        return Response(status_code=204)
    run = steward_assist.open_child_run(
        db, attempt_id=grant["attempt_id"], lease_owner=body.leased_by
    )
    if run is None:
        return Response(status_code=204)
    run_token = agent_tokens.issue_run_token(
        run_id=run.id,
        job_id=grant["steward_job_id"],
        attempt=run.attempt,
        agent_kind="steward",
        space_id=grant["space_id"],
        tool_allowlist=list(run.tool_allowlist_json or []),
        steward_attempt_id=grant["attempt_id"],
        viewer_account_id=grant["viewer_account_id"],
    )
    return StewardLeaseOut(
        run_id=run.id,
        # Both carry the parent StewardJob id. `job_id` is the protocol-wide field
        # the sidecar decodes and heartbeats against; `steward_job_id` keeps the
        # explicit "authorization root" name. Omitting `job_id` made the sidecar
        # heartbeat `/jobs/undefined/heartbeat`.
        job_id=grant["steward_job_id"],
        steward_job_id=grant["steward_job_id"],
        assist_attempt_id=grant["attempt_id"],
        assist_kind=grant["assist_kind"],
        agent_kind="steward",
        attempt=run.attempt,
        tool_allowlist=list(run.tool_allowlist_json or []),
        policy_version=grant["policy_version"],
        max_concurrent=config.STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE,
        run_token=run_token,
    )


def _reissue_run_token(claims: dict[str, Any]) -> str:
    """按**原 token 的 claims** 重新签发一个 run token。

    为何需要：run token 只在租约时签发一次，而 `AGENT_RUN_TOKEN_TTL_SECONDS_MAX`
    是 600s。租约本身随心跳续期，token 不续，于是任何超过 10 分钟的 run 都会在
    下一次心跳时收到 401；sidecar 把 401 当作租约失效并 abort（实测 run 232/244
    的 401 均出现在 token 到期后 8–18 秒内）。

    只重签，不改变 claims：身份、空间、attempt、viewer、allowlist 全部沿用已校验
    过的原 token，因此续签不会扩大权限，也不绕过任何 fence。
    """
    return agent_tokens.issue_run_token(
        run_id=claims["run_id"],
        job_id=claims["job_id"],
        attempt=claims["attempt"],
        agent_kind=claims["agent_kind"],
        space_id=claims["space_id"],
        tool_allowlist=list(claims["tool_allowlist"]),
        # per-kind 必含集不同：assistant 必须给 account_id 且不得给 steward 专属
        # 字段；steward 反之。逐个按 kind 传，避免用 None 触发 fail-closed。
        account_id=claims.get("account_id") if claims["agent_kind"] == "assistant" else None,
        steward_attempt_id=(
            claims.get("steward_attempt_id") if claims["agent_kind"] == "steward" else None
        ),
        viewer_account_id=(
            claims.get("viewer_account_id") if claims["agent_kind"] == "steward" else None
        ),
    )


@router.post("/jobs/{job_id}/heartbeat", response_model=HeartbeatOut)
def heartbeat_job(
    job_id: int,
    request: Request,
    body: HeartbeatRequest | None = None,
    db: Session = Depends(get_db),
) -> HeartbeatOut:
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["job_id"] != job_id:
        _deny(
            db,
            request,
            reason="job_id_mismatch",
            status_code=403,
            code=AGENT_TOKEN_SCOPE_MISMATCH,
            message="token 与目标 Job 不匹配",
        )
    if claims["agent_kind"] == "steward":
        # Steward child run 无 AgentJob（token 的 job_id 是 StewardJob.id）。
        # 续租必须**同时**续 run 与 attempt：attempt lease 是写回栅栏看的租约，
        # 只续一个会让另一个先过期。
        ttl = body.lease_ttl_seconds if body is not None else None
        steward_identity = StewardExecution.from_claims(claims)
        expires, cancel_requested = steward_assist.heartbeat_child_run(
            db, steward_identity, ttl_seconds=ttl
        )
        # 续签 run token：token 只在租约时签发一次，而 TTL 上限是 600s。
        # 不续签则任何超过 10 分钟的 run 都会在心跳时收到 401，被 sidecar
        # 当作租约失效（实测 run 232/244 均如此）。
        return HeartbeatOut(
            ok=True,
            lease_expires_at=expires,
            cancel_requested=cancel_requested,
            run_token=_reissue_run_token(claims),
        )
    run, _agent_session, _claims = _authorize_run(db, request, int(claims["run_id"]))
    _require_active_run(db, request, run)
    job = db.get(AgentJob, job_id)
    if job is None or job.run_id != claims["run_id"]:
        raise_api_error(404, AGENT_JOB_NOT_FOUND, "Job 不存在或不属于该 Run")
    ttl = body.lease_ttl_seconds if body is not None else None
    expires = agent_queue.heartbeat(
        db, job, ttl_seconds=ttl, execution=ExecutionIdentity.from_claims(_claims)
    )
    # additive：cancel_requested 随续租下发（B2 客户端忽略未知字段，兼容）
    active_run = db.get(AgentRun, claims["run_id"])
    return HeartbeatOut(
        ok=True,
        lease_expires_at=expires,
        cancel_requested=bool(active_run.cancel_requested) if active_run is not None else False,
        # 同 steward：心跳续签 token，避免长 run 因 token 过期被误判失租。
        run_token=_reissue_run_token(claims),
    )


# ---- provider proxy（P1 唯一 egress：sidecar 经此调用云端 Provider）----


@router.post("/runs/{run_id}/provider/chat/completions")
@router.post("/runs/{run_id}/provider/responses")
async def proxy_provider_chat_completions(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """把 sidecar 的模型请求代理到已注册 Provider（服务端解密 + 唯一外网 egress）。

    认证与 run 级 scope 核验复用 run token 合同；Run 必须处于活跃状态。
    上游错误一律脱敏为通用错误体；成功响应（含 SSE 流）原样透传。
    """
    from app.services import provider_proxy
    from app.services.agent_execution import execution_from_claims

    # 租户名额只覆盖**建连阶段**（授权 + 解密解析 + 建立上游连接），这是本端点里
    # 真正消耗工作线程与数据库连接的部分。流本身是异步的、不占工作线程也不应持有
    # 连接（`test_stream_does_not_pin_a_pool_connection_between_chunks` 锁定了这一
    # 点），所以不把名额跨整个流持有——否则会把一次数分钟的流变成一个租户额度，
    # 既拖住同租户的工具调用，也重新引入「请求级 Session 跨 chunk 持连接」。
    #
    # 已知边界（未覆盖）：上游**并发流数**本身不受本层限制，因此一个租户仍可同时
    # 持有多个已建立的上游流。要限制它需要流级配额，属于后续阶段。
    provider_tenant = _peek_execution_tenant(request)
    if provider_tenant is not None:
        await _acquire_execution_slot(
            "agent_provider",
            provider_tenant,
            route="/internal/agent/runs/{run_id}/provider",
            run_id=run_id,
        )
    try:
        # Both kinds may call the gateway: it is the only egress, and a steward
        # child run that cannot reach it has no way to make a model call at all.
        # The token decides which authorization applies, and the kind assertion
        # inside each authorizer keeps the two check sets from contaminating each
        # other.
        run, space_id, _claims = _authorize_provider_run(db, request, run_id)
        expected_api = (
            "openai-responses"
            if request.url.path.endswith("/provider/responses")
            else "openai-completions"
        )
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > config.AGENT_PROVIDER_PROXY_MAX_BYTES:
                    raise_api_error(413, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 请求体过大")
            except ValueError:
                raise_api_error(422, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 请求长度无效")
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > config.AGENT_PROVIDER_PROXY_MAX_BYTES:
                raise_api_error(413, AGENT_PROVIDER_PROXY_UNAVAILABLE, "Provider 请求体过大")
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            (
                client,
                upstream,
                provider_id,
                header_ms,
            ) = await provider_proxy.stream_provider_response(
                db,
                run=run,
                space_id=space_id,
                body=body,
                content_type=request.headers.get("content-type"),
                accept=request.headers.get("accept"),
                user_agent=request.headers.get("user-agent"),
                expected_api=expected_api,
                execution=execution_from_claims(_claims),
            )
        except provider_proxy.ProviderProxyError as exc:
            db.commit()  # 审计先提交（拒绝路径惯例）
            # 安全重试提示（例如永久错误的 x-should-retry:false）必须真的下发，
            # 否则 sidecar 会按 5xx 继续重试。机器可读 detail（如 cancel_requested）
            # 同理：sidecar 靠它区分「服务端已裁决取消」与普通协议冲突。
            raise_api_error(
                exc.status_code, exc.code, exc.message, detail=exc.detail, headers=exc.headers
            )
    finally:
        # 建连阶段结束即归还租户名额；下面的流式转发不再持有它。
        if provider_tenant is not None:
            _execution_admission_limiter("agent_provider").release(provider_tenant)
            await redis_accel.clear_over_quota_async(
                tenant=provider_tenant, resource="agent_provider"
            )
        if provider_tenant is not None and _CLUSTER_RESOURCE.get("agent_provider"):
            _release_cluster_slot_soon(_CLUSTER_RESOURCE["agent_provider"])

    # ---- 流级名额（C4）----
    #
    # 与建连名额分开、且**覆盖流的整个生命周期**。理由：建连名额在上面已归还，
    # 所以一个租户可以同时持有多个**已建立**的长流；而一个 100 秒的流不占工作
    # 线程也不占连接（`test_stream_does_not_pin_a_pool_connection_between_chunks`），
    # 因此没有任何既有名额能限制「同时有多少个上游流在跑」。
    #
    # 取得失败即 503（与建连拒绝同一个错误码）：这是有界拒绝，不是排队——
    # 流已经建好连接，再排队会把上游连接悬着，代价远高于直接拒绝。
    stream_tenant = _peek_execution_tenant(request)
    stream_kind, stream_id = _split_tenant(stream_tenant)
    stream_slot = await _try_acquire_stream_slot(tenant_kind=stream_kind, tenant_id=stream_id)
    if stream_slot is False:
        await upstream.aclose()
        logger.warning(
            "agent stream admission rejected run_id=%d tenant_kind=%s reason=stream_full",
            run_id,
            stream_kind,
        )
        raise_api_error(503, AGENT_EXECUTION_BUSY, "执行资源繁忙，请稍后重试")
    media_type = upstream.headers.get("content-type", "application/json")

    async def _stream_with_slot() -> Any:
        """转发流，并保证流级名额在**流真正结束**时归还。

        放在生成器的 finally 里而不是端点内：StreamingResponse 返回后端点即结束，
        但流还在跑；只有生成器结束（正常读完、客户端断开、上游中断、deadline）
        才意味着这个名额不再被占用。
        """
        try:
            async for chunk in provider_proxy.passthrough_with_audit(
                db,
                run=run,
                provider_id=provider_id,
                client=client,
                upstream=upstream,
                on_finish=db.commit,
                header_ms=header_ms,
                max_duration_seconds=config.AGENT_STREAM_MAX_DURATION_SECONDS,
            ):
                yield chunk
        finally:
            if stream_slot is not None:
                _release_stream_slot_soon(stream_kind, stream_id)

    return StreamingResponse(
        _stream_with_slot(),
        status_code=upstream.status_code,
        media_type=media_type,
    )


# ---- runs ----


@router.get("/runs/{run_id}/context", response_model=ContextOut)
def run_context(run_id: int, request: Request, db: Session = Depends(get_db)) -> ContextOut:
    """session scope、最近消息投影与 Provider 运行期解析（仅下发代理路径）。

    Provider 凭据只在 ProviderGateway 内解密并注入上游 Authorization；context
    仅返回站内代理路径和无密钥 projection，绝不出现在浏览器 API、SSE、领域事件或日志。
    """
    # RT-3 ordering: reject a user JWT as forbidden *before* decoding, so the
    # kind peek below cannot turn a 403 into a 401.
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["agent_kind"] == "steward":
        return _steward_run_context(db, request, run_id, claims)
    run, agent_session, _claims = _authorize_run(db, request, run_id)
    execution = ExecutionIdentity.from_claims(_claims)
    run, agent_session, _job = fence_assistant_execution(db, execution, allow_cancel_requested=True)
    # A Pi session is stateful across turns.  Project the complete durable
    # transcript in stable id order; truncating to a recent-N window silently
    # drops earlier user/assistant turns and can make the model contradict its
    # own conversation.  Context/RAG blocks remain independently bounded by
    # their service-level contracts.
    recent = list(
        db.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == agent_session.id)
            .order_by(AgentMessage.id.asc())
        )
    )
    resolution = agent_provider.resolve_for_run(db, run, agent_session.space_id)
    # P1 唯一 egress：不再向 sidecar 下发解密凭据/base_url，只下发代理路径；
    # 模型流量经 POST /runs/{id}/provider/chat/completions 由服务端转发。
    proxy_base_url = (
        agent_provider_proxy_base_url(run.id) if resolution.policy_result == "allowed" else None
    )
    context_build_id: int | None = None
    context_blocks: list[dict[str, object]] = []
    current_message = next(
        (
            m
            for m in reversed(recent)
            if m.role == "user"
            and (m.id == run.message_id if run.message_id is not None else True)
            and isinstance(m.content_json.get("text"), str)
        ),
        None,
    )
    latest_text = current_message.content_json.get("text") if current_message is not None else None
    # Planning alone sees the last four permitted messages preceding this
    # run's user message. Pi still receives the full authorized text history.
    anchor_history = [
        m.content_json["text"]
        for m in recent
        if current_message is not None
        and m.id < current_message.id
        and m.role in ("user", "assistant")
        and isinstance(m.content_json.get("text"), str)
    ][-4:]
    if isinstance(latest_text, str) and latest_text.strip():
        actor_account = db.get(Account, agent_session.account_id)
        if actor_account is not None:
            try:
                built = context_builder.ContextBuilder(db).build(
                    actor=actor_account.user,
                    space_id=agent_session.space_id,
                    agent_kind=agent_session.agent_kind,
                    query=latest_text,
                    run_id=run.id,
                    provider_kind=resolution.kind,
                    policy_version=run.policy_version,
                    attempt=execution.expected_attempt,
                    execution=execution,
                    recent_messages=anchor_history,
                    provider_decision={
                        "provider_id": resolution.provider_id,
                        "model": resolution.model,
                        "policy_result": resolution.policy_result,
                    },
                )
            except FastAPIHTTPException as exc:
                error = extract_api_error(exc.detail) or {}
                if error.get("code") == AGENT_CONTEXT_INVALIDATED:
                    db.commit()
                else:
                    db.rollback()
                raise
            context_build_id = built.build_id
            context_blocks = (
                policy_guard.enforce(policy_guard.context_hook(built.as_data_blocks())) or []
            )
            # Commit the build and the fully materialized response together below.
    response = ContextOut(
        run_id=run.id,
        session_id=agent_session.id,
        agent_kind=agent_session.agent_kind,
        account_id=agent_session.account_id,
        space_id=agent_session.space_id,
        status=run.status,
        attempt=run.attempt,
        policy_version=run.policy_version,
        tool_allowlist=list(run.tool_allowlist_json),
        messages=[
            ContextMessageOut(
                id=m.id,
                role=m.role,
                content_json={"text": m.content_json["text"]}
                if m.role in ("user", "assistant") and isinstance(m.content_json.get("text"), str)
                else {},
                created_at=m.created_at,
            )
            for m in recent
        ],
        provider=ContextProviderOut(
            provider_id=resolution.provider_id,
            provider_name=resolution.provider_name,
            model=resolution.model,
            kind=resolution.kind,
            api=resolution.api,
            compat=dict(resolution.compat),
            context_window=resolution.context_window,
            max_tokens=resolution.max_tokens,
            reasoning=resolution.reasoning,
            input_modalities=list(resolution.input_modalities),
            thinking_levels=list(resolution.thinking_levels),
            policy_result=resolution.policy_result,
            secret_ref=resolution.secret_ref,
            base_url=proxy_base_url,
            api_key=None,
        ),
        next_event_seq=agent_events.next_seq(db, run.id),
        cancel_requested=bool(run.cancel_requested),
        context_build_id=context_build_id,
        context_blocks=context_blocks,
    )
    db.commit()
    return response


@router.post("/runs/{run_id}/events/append", response_model=EventAppendOut)
def append_events_endpoint(
    run_id: int,
    body: EventAppendRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> EventAppendOut:
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["agent_kind"] == "steward":
        run, _steward_claims = _authorize_steward_run(db, request, run_id)
        # The steward fence is the same one every other run-scoped endpoint uses,
        # so lease/cancel/batch state cannot diverge between append and settle.
        fence_steward_execution(
            db, StewardExecution.from_claims(claims), allow_cancel_requested=True
        )
        execution: ExecutionIdentity | StewardExecution = StewardExecution.from_claims(claims)
    else:
        run, _agent_session, _claims = _authorize_run(db, request, run_id)
        execution = ExecutionIdentity.from_claims(_claims)
        _require_active_run(db, request, run)
    # 类型先于事务校验：未知类型不落公开流，直接审计拒绝
    for entry in body.events:
        if entry.type not in agent_events.EVENT_TYPES:
            _audit_and_raise(
                db,
                action="agent_event_rejected",
                target_id=run_id,
                detail={"reason": "unknown_type", "seq": entry.seq},
                status_code=422,
                code=AGENT_EVENT_INVALID,
                message="未知事件类型",
                api_detail={"type": entry.type},
            )
    entries = [
        EventEntry(
            seq=e.seq,
            type=e.type,
            public_payload=e.public_payload,
            context_reference=e.context_reference.model_dump() if e.context_reference else None,
            timing=e.timing.model_dump(exclude_none=True) if e.timing else None,
        )
        for e in body.events
    ]
    try:
        accepted, duplicates = agent_events.append_events(db, run, entries, execution=execution)
    except FastAPIHTTPException as exc:
        # Event/message/fingerprint writes still roll back as a whole. A
        # source/policy invalidation already observed by the server is durable
        # even if a later entry rejects this batch; it cannot revive on retry.
        context_builder.rollback_preserving_invalidation(db, execution)
        api_error = extract_api_error(exc.detail) or {}
        audit.write_audit(
            db,
            action="agent_event_rejected",
            actor_id=None,
            target_id=run_id,
            detail={"reason": str(api_error.get("code") or "conflict")},
        )
        db.commit()
        raise
    db.commit()
    # 先持久化再广播（RT-4）：通知仅作实时性优化，跨进程靠 SSE 轮询/重连回放兑底
    agent_events.notifier.publish(run_id)
    return EventAppendOut(
        accepted=[EventAcceptedOut(seq=row.seq, event_id=row.id) for row in accepted],
        duplicates=duplicates,
    )


# ---- 工具执行准入（09-30：连接池/工作线程饥饿）----
#
# 每个工具请求都要写库（fence 写锁 → 准入 CAS → 幂等占位 → 审计），所以一个在途
# 工具调用 = 1 个连接池连接 + 1 个工作线程。连接池上限（`app.db.POOL_MAX_CONNECTIONS`
# = 15）**小于** AnyIO 工作线程上限（40），于是池被占满后，等待连接的请求会一直
# **占着工作线程**直到 `pool_timeout`；足够多的这种请求即让心跳/lease/health 一起
# 拿不到线程（09-30 受控复现：40 个请求全部耗时精确等于 pool_timeout，期间
# anyio borrowed/total 持续 40/40）。
#
# 因此准入放在**事件循环**上：等名额既不占工作线程也不占数据库连接，拿到名额后
# 才进入工作线程并打开自己的 Session。名额是进程级的，上限必须明显小于连接池
# 上限（校验见 `config._validate_agent_tool_admission`），为其他端点留出余量。
_TOOL_ADMISSION_WARN_SECONDS = 1.0
_TOOL_EXECUTE_ROUTE = "/internal/agent/runs/{run_id}/tools/{tool_name}/execute"

_slot_counts_lock = threading.Lock()
_slot_active = 0
_slot_waiting = 0

# Semaphore 在首次 await 时绑定当时的 loop。生产只有一份事件循环（`app.serve`
# 的三个 listener 跑在同一 loop 上，因而共享同一份 AnyIO 工作线程预算），但测试
# 会为每个 TestClient 起新 loop，所以按 loop 各持一份，否则第二个 loop 上会抛
# “bound to a different event loop”。
_slot_semaphore: asyncio.Semaphore | None = None
_slot_semaphore_loop: weakref.ReferenceType[asyncio.AbstractEventLoop] | None = None
_slot_semaphore_lock = threading.Lock()


def _tool_slot_limiter() -> asyncio.Semaphore:
    """当前事件循环的工具执行名额上限（进程级共享，按 loop 各持一份）。"""
    global _slot_semaphore, _slot_semaphore_loop
    loop = asyncio.get_running_loop()
    with _slot_semaphore_lock:
        bound = _slot_semaphore_loop() if _slot_semaphore_loop is not None else None
        if _slot_semaphore is None or bound is not loop:
            _slot_semaphore = asyncio.Semaphore(config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS)
            _slot_semaphore_loop = weakref.ref(loop)
        return _slot_semaphore


def _count_slots(*, active: int = 0, waiting: int = 0) -> tuple[int, int]:
    """更新并在锁内读出（活跃, 等待）计数，仅供诊断日志使用。"""
    global _slot_active, _slot_waiting
    with _slot_counts_lock:
        _slot_active += active
        _slot_waiting += waiting
        return _slot_active, _slot_waiting


async def _acquire_tool_slot(run_id: int) -> asyncio.Semaphore:
    """在事件循环上等一个执行名额；等待期间不占工作线程，也不占连接。

    协程在等待中被取消时由 `Semaphore.acquire` 自身把等待者移出队列，且不消耗
    名额，所以这里只需保证“拿到名额才计数/才归还”。
    """
    limiter = _tool_slot_limiter()
    _count_slots(waiting=1)
    started = time.perf_counter()
    try:
        await limiter.acquire()
    finally:
        _count_slots(waiting=-1)
    waited = time.perf_counter() - started
    active, waiting = _count_slots(active=1)
    if waited >= _TOOL_ADMISSION_WARN_SECONDS:
        # 只记实际测量的阶段与安全字段（阶段名/路由模板/run id/时长/计数）：
        # 不含 payload、凭据、SQL 参数，也不把总响应时间说成“线程池耗尽”。
        logger.warning(
            "agent tool admission wait stage=tool_admission_wait route=%s run_id=%d "
            "wait_ms=%.1f active=%d waiting=%d cap=%d",
            _TOOL_EXECUTE_ROUTE,
            run_id,
            waited * 1000.0,
            active,
            waiting,
            config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS,
        )
    return limiter


def _release_tool_slot(limiter: asyncio.Semaphore) -> None:
    _count_slots(active=-1)
    limiter.release()


# 执行面准入：全局 + 租户双层。与工具准入**分开**：工具准入限制的是「同时在跑的
# 工具」，这里限制的是「一个租户同时在跑的执行单元」，两者共同保证控制面余量。
#
# 租户键按 kind 取：Assistant=account（同一用户跨空间共享一份额度，这是有意的——
# 他就是同一个人的并发预算）；Steward=space（空间级工作不能因为 viewer 不同而
# 共享或扩大额度）。键里不放用户可识别内容，只放整数 id。
# 每个**执行平面**各持一个准入器。平面必须分开，否则会把两种时间尺度绑在一起：
# 一次 provider 流可以持续数分钟，而工具调用是毫秒级；共用一个额度会让一个长流
# 把同一租户的工具调用全部挡住，反之亦然。控制面不在这套额度内。
_execution_limiters: dict[str, ResourceLimiter] = {}
_execution_limiters_loop: weakref.ReferenceType[asyncio.AbstractEventLoop] | None = None
_execution_limiters_lock = threading.Lock()


def _execution_admission_limiter(resource: str) -> ResourceLimiter:
    """取某个执行平面的准入器（按事件循环各持一份，理由同工具名额）。

    `resource` 只能是下面登记的平面名；未知名字直接失败，避免拼错时静默地
    每个请求各建一个准入器（那样配额等于不存在）。
    """
    global _execution_limiters_loop
    if resource not in _EXECUTION_PLANES:
        raise RuntimeError(f"unknown execution plane: {resource}")
    loop = asyncio.get_running_loop()
    with _execution_limiters_lock:
        bound = _execution_limiters_loop() if _execution_limiters_loop is not None else None
        if bound is not loop:
            _execution_limiters.clear()
            _execution_limiters_loop = weakref.ref(loop)
        limiter = _execution_limiters.get(resource)
        if limiter is None:
            limiter = ResourceLimiter(
                name=resource,
                global_capacity=config.AGENT_EXECUTION_GLOBAL_CAPACITY,
                per_tenant_capacity=config.AGENT_EXECUTION_PER_TENANT_CAPACITY,
                max_wait_seconds=config.AGENT_EXECUTION_MAX_WAIT_SECONDS,
                max_queue=config.AGENT_EXECUTION_MAX_QUEUE,
            )
            _execution_limiters[resource] = limiter
        return limiter


def execution_tenant_key(claims: dict[str, Any]) -> str:
    """从已校验的 run token claims 派生租户键。

    Assistant 用 account_id，Steward 用 space_id：这与两个 kind 的授权主体一致
    （Assistant 是账号锚定，Steward 是空间锚定），因此额度不会跨主体串味。
    两个 claim 都经 `decode_run_token` 按 kind 校验过类型，这里只做读取。
    """
    if claims.get("agent_kind") == "steward":
        return f"space:{claims['space_id']}"
    return f"account:{claims['account_id']}"


def _split_tenant(tenant: str | None) -> tuple[str, int]:
    """把 `account:12` / `space:7` 拆成 (kind, id)；无法解析时返回 ("account", 0)。

    无法解析只在 token 缺失时发生（`_peek_execution_tenant` 返回 None），那时
    流级名额按未登记处理——真正的授权仍在工作线程内的原路径上执行。
    """
    if not tenant or ":" not in tenant:
        return "account", 0
    kind, _, raw = tenant.partition(":")
    try:
        return kind, int(raw)
    except ValueError:
        return "account", 0


async def _try_acquire_stream_slot(*, tenant_kind: str, tenant_id: int) -> bool | None:
    """在工作线程里尝试占用一个流级名额（同步 DB 工作不得在事件循环上做）。"""
    from anyio import to_thread

    def _attempt() -> bool | None:
        db = SessionLocal()
        try:
            return capacity.try_acquire_stream(
                db,
                tenant_kind=tenant_kind,
                tenant_id=tenant_id,
                capacity_tenant=config.AGENT_STREAM_PER_TENANT_CAPACITY,
            )
        finally:
            db.commit()
            db.close()

    return await to_thread.run_sync(_attempt)


def _release_stream_slot(tenant_kind: str, tenant_id: int) -> None:
    """同步归还流级名额（供已在工作线程内的调用方使用）。"""
    db = SessionLocal()
    try:
        capacity.release_stream(db, tenant_kind=tenant_kind, tenant_id=tenant_id)
    finally:
        db.commit()
        db.close()


def _release_stream_slot_soon(tenant_kind: str, tenant_id: int) -> None:
    """从事件循环调度流级名额归还（同 `_release_cluster_slot_soon` 的理由）。"""
    from anyio import to_thread

    task = asyncio.ensure_future(to_thread.run_sync(_release_stream_slot, tenant_kind, tenant_id))

    def _swallow(fut: asyncio.Future[None]) -> None:
        if not fut.cancelled():
            fut.exception()

    task.add_done_callback(_swallow)


# 执行平面名：只用于准入记账与诊断，不含租户数据。
_EXECUTION_PLANES = frozenset({"agent_tool", "agent_provider"})


async def _acquire_execution_slot(resource: str, tenant: str, *, route: str, run_id: int) -> None:
    """在事件循环上等一个执行名额；超时/队列满则有界拒绝（503 + 明确错误码）。

    控制面端点**不调用**本函数：heartbeat/lease/settle/cancel/context/health 必须
    始终可执行，这正是保留余量的目的。
    """
    # Redis 负缓存快速拒绝（C5 接入）。
    #
    # 只做**拒绝**：命中标记说明该租户近期被权威路径判为已满，直接 503，
    # 不打数据库、不进排队。标记不存在时照常走权威路径——因此 Redis 故障
    # （`get` 返回 None）等价于「没有标记」，行为退化为未接入状态，
    # **不会** fail-open，也不会改变任何授权语义。
    if await redis_accel.is_marked_over_quota_async(tenant=tenant, resource=resource):
        logger.warning(
            "agent execution admission rejected resource=%s route=%s run_id=%d "
            "tenant_kind=%s reason=redis_over_quota_cache",
            resource,
            route,
            run_id,
            tenant.split(":", 1)[0],
        )
        raise_api_error(503, AGENT_EXECUTION_BUSY, "执行资源繁忙，请稍后重试")

    limiter = _execution_admission_limiter(resource)
    started = time.perf_counter()
    try:
        await limiter.acquire(tenant)
    except AdmissionRejected as rejected:
        # 权威路径判定已满：写负缓存，使该租户的后续突发不再逐个打数据库。
        await redis_accel.mark_over_quota_async(tenant=tenant, resource=resource)
        snapshot = limiter.snapshot()
        logger.warning(
            "agent execution admission rejected resource=%s route=%s run_id=%d tenant_kind=%s "
            "waited_ms=%.1f reason=%s active=%d waiting=%d global_cap=%d tenant_cap=%d",
            resource,
            route,
            run_id,
            tenant.split(":", 1)[0],
            rejected.waited_seconds * 1000.0,
            rejected.reason,
            snapshot.active,
            snapshot.waiting,
            snapshot.global_capacity,
            snapshot.per_tenant_capacity,
        )
        raise_api_error(
            503,
            AGENT_EXECUTION_BUSY,
            "执行资源繁忙，请稍后重试",
        )
    # 集群级上限：进程内 limiter 只能约束**本实例**。两个实例各自
    # global_capacity=2 时集群实际并发可达 4（实测 `cross_instance_capacity.py`：
    # 观测 4，配置 2）。因此拿到本实例名额后，再尝试占一个**持久化**名额；
    # 失败说明全集群已满，必须释放本实例名额并给出同一个有界拒绝。
    #
    # 未登记（部署未 bootstrap）时返回 None，按「无限」处理——未启用集群层的部署
    # 行为与改动前逐字一致。
    cluster_kind = _CLUSTER_RESOURCE.get(resource)
    if cluster_kind is not None:
        acquired_cluster = await _try_acquire_cluster_slot(cluster_kind)
        if acquired_cluster is False:
            limiter.release(tenant)
            await redis_accel.mark_over_quota_async(tenant=tenant, resource=resource)
            logger.warning(
                "agent execution admission rejected resource=%s route=%s run_id=%d "
                "tenant_kind=%s reason=cluster_full",
                resource,
                route,
                run_id,
                tenant.split(":", 1)[0],
            )
            raise_api_error(503, AGENT_EXECUTION_BUSY, "执行资源繁忙，请稍后重试")

    waited = time.perf_counter() - started
    if waited >= _TOOL_ADMISSION_WARN_SECONDS:
        snapshot = limiter.snapshot()
        logger.warning(
            "agent execution admission wait resource=%s route=%s run_id=%d tenant_kind=%s "
            "wait_ms=%.1f active=%d waiting=%d global_cap=%d tenant_cap=%d",
            resource,
            route,
            run_id,
            tenant.split(":", 1)[0],
            waited * 1000.0,
            snapshot.active,
            snapshot.waiting,
            snapshot.global_capacity,
            snapshot.per_tenant_capacity,
        )


# 执行平面 -> 集群级 counter 资源名。只有登记在此的平面才受集群上限约束；
# 控制面端点不取名额，因此不出现在这里（这正是保留余量的机制）。
_CLUSTER_RESOURCE = {
    "agent_provider": capacity.RESOURCE_CLUSTER_PROVIDER,
    "agent_tool": capacity.RESOURCE_CLUSTER_TOOL,
}


async def _try_acquire_cluster_slot(resource_kind: str) -> bool | None:
    """在**工作线程**里尝试占用一个集群级名额。

    必须在工作线程内执行：它要取数据库连接并开事务，在事件循环上做会重演
    `09-30` 那次的连接池饥饿（同步 DB 工作阻塞事件循环）。短事务、无网络 I/O。

    返回 `None` 表示该资源未登记 counter（部署未 bootstrap）→ 不限制。
    """
    from anyio import to_thread

    def _attempt() -> bool | None:
        db = SessionLocal()
        try:
            return capacity.try_acquire_cluster(db, resource_kind=resource_kind)
        finally:
            db.commit()
            db.close()

    return await to_thread.run_sync(_attempt)


def _clear_over_quota_soon(*, tenant: str, resource: str) -> None:
    """从事件循环调度负缓存清理（同 `_release_cluster_slot_soon` 的理由）。"""
    from anyio import to_thread

    task = asyncio.ensure_future(
        to_thread.run_sync(lambda: redis_accel.clear_over_quota(tenant=tenant, resource=resource))
    )

    def _swallow(fut: asyncio.Future[bool]) -> None:
        if not fut.cancelled():
            fut.exception()

    task.add_done_callback(_swallow)


def _release_cluster_slot(resource_kind: str) -> None:
    """归还集群级名额（同步版本，供已在工作线程内的调用方使用）。"""
    db = SessionLocal()
    try:
        capacity.release_cluster(db, resource_kind=resource_kind)
    finally:
        db.commit()
        db.close()


def _release_cluster_slot_soon(resource_kind: str) -> None:
    """从**事件循环**调度集群名额归还。

    done-callback 在事件循环上执行，同步 DB 工作会阻塞它（09-30 的教训），
    因此必须转到工作线程。
    """
    from anyio import to_thread

    task = asyncio.ensure_future(to_thread.run_sync(_release_cluster_slot, resource_kind))

    def _swallow(fut: asyncio.Future[None]) -> None:
        # 取回异常，避免 never-retrieved 告警；归还是幂等的，失败由计数行对账发现。
        if not fut.cancelled():
            fut.exception()

    task.add_done_callback(_swallow)


def _peek_execution_tenant(request: Request) -> str | None:
    """在事件循环上解析 run token 并派生租户键，用于取名额**之前**排队。

    这里只做密码学校验（无数据库访问、无审计写），所以可以在事件循环上安全调用；
    真正的授权、fence、撤权检查仍在工作线程内的原路径上执行，语义不变。

    解析失败返回 None：让请求按原路径进入工作线程并得到既有 401，而不是在这里
    改变错误语义。
    """
    raw = _bearer_token(request)
    if raw is None:
        return None
    try:
        claims = agent_tokens.decode_run_token(raw)
    except agent_tokens.AgentTokenError:
        return None
    try:
        return execution_tenant_key(claims)
    except KeyError:
        # claims 缺 kind 所需字段：同样交给原路径按既有契约拒绝。
        return None


def _execute_tool_body(
    run_id: int, tool_name: str, body: ToolExecuteRequest, request: Request
) -> ToolExecuteOut:
    """同步工具体：在**自己的 Session** 内完成，绝不跨线程复用请求级 Session。

    `get_db` 的请求级 Session 属于事件循环所在线程；工作线程里新建 → 授权 →
    policy → `agent_tools.execute` → commit → `finally` 关闭，连接随之归还连接池。
    """
    db = SessionLocal()
    try:
        return _execute_tool_in_session(db, run_id, tool_name, body, request)
    finally:
        db.close()


def _execute_tool_in_session(
    db: Session, run_id: int, tool_name: str, body: ToolExecuteRequest, request: Request
) -> ToolExecuteOut:
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["agent_kind"] == "steward":
        run, _claims = _authorize_steward_run(db, request, run_id)
        agent_session = None
        execution: ExecutionIdentity | StewardExecution = StewardExecution.from_claims(claims)
    else:
        run, agent_session, _claims = _authorize_run(db, request, run_id)
        execution = ExecutionIdentity.from_claims(claims)
    decision = policy_guard.tool_call_hook(
        tool=tool_name,
        version=body.version,
        arguments=body.input,
        allowlist=claims["tool_allowlist"],
    )
    policy_guard.enforce(decision, code="POLICY_TOOL_BLOCKED")
    # running 态门禁与四类拒绝码在服务层统一执行并写审计
    output = agent_tools.execute(
        db,
        run,
        agent_session,
        claims,
        name=tool_name,
        version=body.version,
        input_payload=body.input,
        tool_call_id=body.tool_call_id,
        execution=execution,
    )
    db.commit()
    result_decision = policy_guard.tool_result_hook(output)
    safe_output = policy_guard.enforce(result_decision, code="POLICY_TOOL_RESULT_BLOCKED")
    if isinstance(safe_output, dict):
        output = safe_output
    return ToolExecuteOut(ok=True, tool=tool_name, version=body.version, output=output)


@router.post("/runs/{run_id}/tools/{tool_name}/execute", response_model=ToolExecuteOut)
async def execute_tool(
    run_id: int,
    tool_name: str,
    body: ToolExecuteRequest,
    request: Request,
) -> ToolExecuteOut:
    """工具执行端点：先在事件循环上等名额，再进工作线程跑同步体。

    等待名额不占工作线程、不占连接，所以工具突发不会把共享资源耗尽到心跳/
    lease/settle/health 也拿不到（09-30 AC-1/AC-2）。授权、fence、准入 CAS、
    幂等与审计语义与改动前逐字相同，只是换到工具自己的 Session 上执行；排队
    后进入执行仍会在 fence 内重新验证身份、租约与权限。
    """
    tenant = _peek_execution_tenant(request)
    if tenant is not None:
        # 执行面租户名额先于工具名额：先让**租户**排队，避免一个租户的工具突发
        # 占满全局工具名额（那正是本次要消除的跨租户挤占）。
        await _acquire_execution_slot(
            "agent_tool", tenant, route=_TOOL_EXECUTE_ROUTE, run_id=run_id
        )
    limiter = await _acquire_tool_slot(run_id)
    dispatched = False
    try:
        work: asyncio.Future[ToolExecuteOut] = asyncio.ensure_future(
            anyio.to_thread.run_sync(_execute_tool_body, run_id, tool_name, body, request)
        )

        def _on_done(future: asyncio.Future[ToolExecuteOut]) -> None:
            # 名额只在 worker 真正结束后归还：协程取消不得提前放行下一个工具请求
            # （否则取消路径上并发上限失效）。顺带取回异常，避免 never-retrieved 告警。
            if not future.cancelled():
                future.exception()
            _release_tool_slot(limiter)
            if tenant is not None:
                # 归还顺序与取得顺序相反：先放工具名额，再放租户名额，最后放集群名额。
                _execution_admission_limiter("agent_tool").release(tenant)
                # done-callback 在事件循环上执行，因此这里也必须调度到线程。
                _clear_over_quota_soon(tenant=tenant, resource="agent_tool")
                _release_cluster_slot_soon(_CLUSTER_RESOURCE["agent_tool"])

        work.add_done_callback(_on_done)
        dispatched = True
        # shield：外层请求被取消（客户端断开）时立即返回，不再等待 worker；worker
        # 仍会跑完，名额由上面的 done-callback 在它真正结束时释放。
        return await asyncio.shield(work)
    finally:
        if not dispatched:
            # 名额已取得但未能派发（例如在 ensure_future 之前被取消）：必须归还。
            _release_tool_slot(limiter)
            if tenant is not None:
                _execution_admission_limiter("agent_tool").release(tenant)
                # done-callback 在事件循环上执行，因此这里也必须调度到线程。
                _clear_over_quota_soon(tenant=tenant, resource="agent_tool")
                _release_cluster_slot_soon(_CLUSTER_RESOURCE["agent_tool"])


@router.post("/runs/{run_id}/settle", response_model=SettleOut)
def settle_run_endpoint(
    run_id: int,
    body: SettleRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SettleOut:
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
    if claims["agent_kind"] == "steward":
        return _settle_steward_run(db, request, run_id, body, claims)
    run, _agent_session, _claims = _authorize_run(db, request, run_id)
    policy_guard.enforce(
        policy_guard.agent_settled(status=body.status), code="POLICY_PROVIDER_BLOCKED"
    )
    try:
        settled = agent_queue.settle_run(
            db,
            run,
            status=body.status,
            error_code=body.error_code,
            error=body.error,
            execution=ExecutionIdentity.from_claims(_claims),
        )
    except FastAPIHTTPException as exc:
        db.rollback()
        api_error = extract_api_error(exc.detail) or {}
        audit.write_audit(
            db,
            action="agent_protocol_violation",
            actor_id=None,
            target_id=run_id,
            detail={"endpoint": "settle", "reason": str(api_error.get("code") or "invalid")},
        )
        db.commit()
        raise
    return SettleOut(
        ok=True,
        run_id=settled.id,
        status=settled.status,
        settled_at=settled.settled_at or timeutil.utcnow(),
    )


def _steward_run_context(
    db: Session, request: Request, run_id: int, claims: dict[str, Any]
) -> ContextOut:
    """Steward child run 的 context：空间级投影、**无会话历史**。

    设计 §8.3：assist 的输入是服务端投影而非对话。引入历史会让「同一语义哈希
    → 同一输出」的幂等与去重（``request_hash_for`` / ``last_checked_hash``）
    失效，因此 ``messages`` 恒为 ``[]``。投影内容由服务端白名单构造（节点代号 +
    已确认事实 id/type/revision），绝不下发姓名或 masked 原值。
    """
    run, _claims = _authorize_steward_run(db, request, run_id)
    identity = StewardExecution.from_claims(claims)
    run, steward_run, _job = fence_steward_execution(db, identity, allow_cancel_requested=True)
    attempt = db.scalar(select(StewardModelCall).where(StewardModelCall.run_id == run.id))
    resolution = agent_provider.resolve_for_run(
        db, run, claims["space_id"], agent_provider.AGENT_KIND_STEWARD
    )
    proxy_base_url = (
        agent_provider_proxy_base_url(run.id) if resolution.policy_result == "allowed" else None
    )
    context_blocks: list[dict[str, object]] = []
    context_build_id: int | None = None
    if attempt is not None:
        # The viewer (terminology only) supplies the actor for the policy
        # identity; the other kinds are space-scoped and use the space admin.
        actor_account_id = steward_run.viewer_account_id
        if actor_account_id is None:
            # Space-scoped kinds have no viewer; the space admin supplies the
            # policy identity. Resolve through the member's user, not an account
            # column (space_members stores user_id).
            admin_user_id = db.scalar(
                select(SpaceMember.user_id).where(
                    SpaceMember.space_id == claims["space_id"],
                    SpaceMember.role == "space_admin",
                    SpaceMember.status == "active",
                )
            )
            if admin_user_id is not None:
                actor_account_id = db.scalar(
                    select(Account.id).where(Account.user_id == admin_user_id)
                )
        actor_account = db.get(Account, actor_account_id) if actor_account_id else None
        if actor_account is not None:
            # steward 的上下文**就是**投影本身（roster + 已确认事实）。过去这里先跑一次
            # 完整 RAG 检索、再整体丢弃它的结果（只留 build_id），造成两个问题：
            #   1. 每次 run 白跑一次检索（候选收集 + 评分 + 写 build/items）；
            #   2. `ContextBuildItem.included=True` 声称纳入了模型从未收到的内容，
            #      审计与实际发送不符。
            # 现在改为显式 `prefetched`：builder 不再检索，纳入项就是投影块本身，
            # 且 `context_blocks` 由同一个 `built.as_data_blocks()` 派生，两处不再各自构造。
            projection = _steward_projection_source(db, attempt)
            built = context_builder.ContextBuilder(db).build(
                actor=actor_account.user,
                space_id=claims["space_id"],
                agent_kind="steward",
                query=attempt.prompt_digest,
                run_id=run.id,
                prefetched=(projection,),
                # steward 的输入不是检索结果，而是一份已由
                # `STEWARD_ASSIST_MAX_PROMPT_BYTES` 限定的确定性投影。再套一层检索预算
                # 只会把它静默整块丢掉（实测：落到默认分层份额 0.2 而全排除）。
                budgeted=False,
                provider_kind=resolution.kind,
                policy_version=run.policy_version,
                attempt=identity.expected_attempt,
                execution=identity,
                provider_decision={
                    "provider_id": resolution.provider_id,
                    "model": resolution.model,
                    "policy_result": resolution.policy_result,
                },
            )
            context_build_id = built.build_id
            # 与 `ContextBuildItem` 同源：不再单独构造一次块列表。
            context_blocks = (
                policy_guard.enforce(policy_guard.context_hook(built.as_data_blocks())) or []
            )
    response = ContextOut(
        run_id=run.id,
        session_id=None,
        agent_kind="steward",
        account_id=None,
        space_id=claims["space_id"],
        status=run.status,
        attempt=run.attempt,
        policy_version=run.policy_version,
        tool_allowlist=list(run.tool_allowlist_json),
        messages=[],
        provider=ContextProviderOut(
            provider_id=resolution.provider_id,
            provider_name=resolution.provider_name,
            model=resolution.model,
            kind=resolution.kind,
            api=resolution.api,
            compat=dict(resolution.compat),
            context_window=resolution.context_window,
            max_tokens=resolution.max_tokens,
            reasoning=resolution.reasoning,
            input_modalities=list(resolution.input_modalities),
            thinking_levels=list(resolution.thinking_levels),
            policy_result=resolution.policy_result,
            secret_ref=None,
            base_url=proxy_base_url,
            api_key=None,
        ),
        context_build_id=context_build_id,
        context_blocks=context_blocks,
        next_event_seq=agent_events.next_seq(db, run.id),
        cancel_requested=bool(run.cancel_requested),
        # The in-process carrier sends this as the system message, so the child run
        # must send the same text: it carries the per-kind rules (candidate
        # direction semantics, ranking's strict permutation, terminology's
        # non-invention clause), and ``prompt_digest`` is computed over it.
        steward_instructions=steward_assist.instructions_for(attempt)
        if attempt is not None
        else None,
    )
    db.commit()
    return response


def _steward_projection_source(
    db: Session, attempt: StewardModelCall
) -> context_builder.ContextSource:
    """把 reserved attempt 的投影包成 `ContextSource`。

    提示词文本本身不离开服务端：sidecar 从这份投影加自己的 system prompt 重建它，
    所以块里装的是结构化输入而不是成品 prompt。

    它取代了旧的 `_steward_projection_blocks`：后者返回 dict，而 builder 只接受
    `ContextSource`，于是「纳入项」（对象）与「发送块」（dict）成了两个真源——
    正是审计与实际发送不符的成因。现在块由 `built.as_data_blocks()` 派生，
    只有一条路径。
    """
    user_content = steward_assist._user_content_for(db, attempt)
    return context_builder.ContextSource(
        source_type="steward_projection",
        source_id=f"attempt:{attempt.id}",
        text=user_content,
        scope="space",
        sensitivity="normal",
        revision=attempt.attempt_no,
        citation_handle=attempt.prompt_digest,
        trust="untrusted_data",
    )


def _lease_owner_for(db: Session, attempt_id: int) -> str:
    """The attempt's current lease owner, used as the settlement capability.

    Read from the row rather than trusted from the request: the sidecar proves
    identity with its run token, and the row is the authority on who holds the
    lease.
    """
    from app.models.steward import StewardModelCall

    attempt = db.get(StewardModelCall, attempt_id)
    return (attempt.lease_owner or "") if attempt is not None else ""


def _settle_steward_run(
    db: Session, request: Request, run_id: int, body: SettleRequest, claims: dict[str, Any]
) -> SettleOut:
    """Settle a Steward child run and its attempt in **one** transaction.

    Why ``on_settled`` instead of two sequential calls: the run's terminal state
    and the attempt's settlement must never be separately observable, or a crash
    between them leaves "run succeeded / attempt still in_flight" — the double
    terminal state R2 forbids. The hook runs inside ``_settle``'s immediate
    transaction, after the terminal write.
    """
    run, _claims = _authorize_steward_run(db, request, run_id)
    identity = StewardExecution.from_claims(claims)
    policy_guard.enforce(
        policy_guard.agent_settled(status=body.status), code="POLICY_PROVIDER_BLOCKED"
    )
    outcome: dict[str, str | None] = {}

    def _hook(session: Session, settled_run: AgentRun) -> None:
        # The attempt is named by the token, so settlement targets it directly
        # rather than re-deriving it from the run.
        attempt_id = claims.get("steward_attempt_id")
        if attempt_id is None:
            outcome["status"] = None
            return
        outcome["status"] = steward_assist.record_attempt_outcome(
            session,
            attempt_id=int(attempt_id),
            status=body.status,
            lease_owner=_lease_owner_for(session, int(attempt_id)),
            error_code=body.error_code,
            text=body.output_text,
            usage=body.usage,
            latency_ms=body.latency_ms or 0,
        )

    try:
        settled = agent_queue.settle_run(
            db,
            run,
            status=body.status,
            error_code=body.error_code,
            error=body.error,
            execution=identity,
            on_settled=_hook,
        )
    except FastAPIHTTPException as exc:
        db.rollback()
        api_error = extract_api_error(exc.detail) or {}
        audit.write_audit(
            db,
            action="agent_protocol_violation",
            actor_id=None,
            target_id=run_id,
            detail={"endpoint": "settle", "reason": str(api_error.get("code") or "invalid")},
        )
        db.commit()
        raise
    # Phase 2, outside the run's transaction: apply the product that phase 1 just
    # made durable. A failure here leaves the product persisted and unapplied,
    # which is crash point ④ and is what recover_stuck_attempts finishes — losing
    # it would discard a paid-for model answer.
    attempt_id = claims.get("steward_attempt_id")
    if attempt_id is not None:
        steward_assist.apply_settled_attempt(db, attempt_id=int(attempt_id))
    return SettleOut(
        ok=True,
        run_id=settled.id,
        status=settled.status,
        settled_at=settled.settled_at or timeutil.utcnow(),
    )
