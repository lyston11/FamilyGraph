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

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, Request, Response
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db
from app.errors import (
    AGENT_CONTEXT_INVALIDATED,
    AGENT_DISABLED,
    AGENT_EVENT_INVALID,
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
    context_builder,
    policy_guard,
    steward_assist,
)
from app.services.agent_events import EventEntry
from app.services.agent_execution import (
    ExecutionIdentity,
    StewardExecution,
    fence_assistant_execution,
    fence_steward_execution,
)
from app.services.provider_proxy import provider_proxy_base_url as agent_provider_proxy_base_url
from app.utils import security, timeutil


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
    if run is None:
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
    grant = steward_assist.lease_attempt(
        db, space_id=body.space_id, worker_id=body.leased_by, ttl_seconds=body.lease_ttl_seconds
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
        return HeartbeatOut(ok=True, lease_expires_at=expires, cancel_requested=cancel_requested)
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

    run, agent_session, _claims = _authorize_run(db, request, run_id)
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
        client, upstream, provider_id, header_ms = await provider_proxy.stream_provider_response(
            db,
            run=run,
            space_id=agent_session.space_id,
            body=body,
            content_type=request.headers.get("content-type"),
            accept=request.headers.get("accept"),
            user_agent=request.headers.get("user-agent"),
            expected_api=expected_api,
            execution=ExecutionIdentity.from_claims(_claims),
        )
    except provider_proxy.ProviderProxyError as exc:
        db.commit()  # 审计先提交（拒绝路径惯例）
        # 安全重试提示（例如永久错误的 x-should-retry:false）必须真的下发，
        # 否则 sidecar 会按 5xx 继续重试。机器可读 detail（如 cancel_requested）
        # 同理：sidecar 靠它区分「服务端已裁决取消」与普通协议冲突。
        raise_api_error(
            exc.status_code, exc.code, exc.message, detail=exc.detail, headers=exc.headers
        )
    media_type = upstream.headers.get("content-type", "application/json")
    return StreamingResponse(
        provider_proxy.passthrough_with_audit(
            db,
            run=run,
            provider_id=provider_id,
            client=client,
            upstream=upstream,
            on_finish=db.commit,
            header_ms=header_ms,
        ),
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


@router.post("/runs/{run_id}/tools/{tool_name}/execute", response_model=ToolExecuteOut)
def execute_tool(
    run_id: int,
    tool_name: str,
    body: ToolExecuteRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ToolExecuteOut:
    run, agent_session, claims = _authorize_run(db, request, run_id)
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
        execution=ExecutionIdentity.from_claims(claims),
    )
    db.commit()
    result_decision = policy_guard.tool_result_hook(output)
    safe_output = policy_guard.enforce(result_decision, code="POLICY_TOOL_RESULT_BLOCKED")
    if isinstance(safe_output, dict):
        output = safe_output
    return ToolExecuteOut(ok=True, tool=tool_name, version=body.version, output=output)


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
            built = context_builder.ContextBuilder(db).build(
                actor=actor_account.user,
                space_id=claims["space_id"],
                agent_kind="steward",
                query=attempt.prompt_digest,
                run_id=run.id,
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
            context_blocks = (
                policy_guard.enforce(
                    policy_guard.context_hook(_steward_projection_blocks(db, attempt))
                )
                or []
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
        steward_prompt_version=steward_assist.STEWARD_PROMPT_VERSION,
    )
    db.commit()
    return response


def _steward_projection_blocks(db: Session, attempt: StewardModelCall) -> list[dict[str, object]]:
    """Build the steward context blocks from the reserved attempt's projection.

    The prompt text itself never leaves the server: the sidecar re-derives it from
    this projection plus its own system prompt, which is why the blocks carry the
    structured input rather than a ready-made prompt.
    """
    user_content = steward_assist._user_content_for(db, attempt)
    return [
        {
            "kind": "data",
            "trust": "untrusted_data",
            "source_type": "steward_projection",
            "source_id": f"attempt:{attempt.id}",
            "scope": "space",
            "sensitivity": "normal",
            "revision": attempt.attempt_no,
            "citation": attempt.prompt_digest,
            "content": user_content,
        }
    ]


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
        outcome["status"] = steward_assist.settle_attempt(
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
    return SettleOut(
        ok=True,
        run_id=settled.id,
        status=settled.status,
        settled_at=settled.settled_at or timeutil.utcnow(),
    )
