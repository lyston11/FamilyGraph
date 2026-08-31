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
    AGENT_DISABLED,
    AGENT_EVENT_INVALID,
    AGENT_INTERNAL_FORBIDDEN,
    AGENT_JOB_NOT_FOUND,
    AGENT_PROVIDER_PROXY_UNAVAILABLE,
    AGENT_RUN_NOT_FOUND,
    AGENT_RUN_NOT_RUNNING,
    AGENT_TOKEN_INVALID,
    AGENT_TOKEN_SCOPE_MISMATCH,
    extract_api_error,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import AgentJob, AgentMessage, AgentRun, AgentSession
from app.models.space import SpaceMember
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
)
from app.services.agent_events import EventEntry
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


def _authorize_run(
    db: Session, request: Request, run_id: int
) -> tuple[AgentRun, AgentSession, dict[str, Any]]:
    """run token 解码 + 与 DB 实体双向核验（scope 五元组 + allowlist）。"""
    _reject_user_jwt(db, request)
    claims = _decode_or_deny(db, request, typ=agent_tokens.RUN_TOKEN_TYPE)
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
        or claims["agent_kind"] != run.kind
        or claims["job_id"] != run.job_id
        or sorted(claims["tool_allowlist"]) != sorted(run.tool_allowlist_json or [])
        or job is None
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
    # directly through the deterministic service; accepting ``steward`` or an
    # omitted kind here would let any holder of the shared service secret
    # consume the Steward queue.
    if body.kind != "assistant":
        _deny(
            db,
            request,
            reason="steward_lease_requires_canonical_worker",
            status_code=403,
            code=AGENT_INTERNAL_FORBIDDEN,
            message="Steward 作业仅可由系统维护 worker 执行",
        )
    grant = agent_queue.lease_next(
        db,
        kind=body.kind or "assistant",
        leased_by=body.leased_by,
        ttl_seconds=body.lease_ttl_seconds,
    )
    if grant is None:
        return Response(status_code=204)
    run_token = agent_tokens.issue_run_token(
        run_id=grant.run.id,
        job_id=grant.job.id,
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
    run, _agent_session, _claims = _authorize_run(db, request, int(claims["run_id"]))
    _require_active_run(db, request, run)
    job = db.get(AgentJob, job_id)
    if job is None or job.run_id != claims["run_id"]:
        raise_api_error(404, AGENT_JOB_NOT_FOUND, "Job 不存在或不属于该 Run")
    ttl = body.lease_ttl_seconds if body is not None else None
    expires = agent_queue.heartbeat(db, job, ttl_seconds=ttl)
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
        client, upstream, provider_id = await provider_proxy.stream_provider_response(
            db,
            run=run,
            space_id=agent_session.space_id,
            body=body,
            content_type=request.headers.get("content-type"),
            accept=request.headers.get("accept"),
            user_agent=request.headers.get("user-agent"),
            expected_api=expected_api,
        )
    except provider_proxy.ProviderProxyError as exc:
        db.commit()  # 审计先提交（拒绝路径惯例）
        raise_api_error(exc.status_code, exc.code, exc.message)
    media_type = upstream.headers.get("content-type", "application/json")
    return StreamingResponse(
        provider_proxy.passthrough_with_audit(
            db,
            run=run,
            provider_id=provider_id,
            client=client,
            upstream=upstream,
            on_finish=db.commit,
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
    run, agent_session, _claims = _authorize_run(db, request, run_id)
    _require_active_run(db, request, run)
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
    latest_text = next(
        (
            message.content_json.get("text")
            for message in reversed(recent)
            if message.role == "user" and isinstance(message.content_json.get("text"), str)
        ),
        None,
    )
    if isinstance(latest_text, str) and latest_text.strip():
        actor_account = db.get(Account, agent_session.account_id)
        if actor_account is not None:
            built = context_builder.ContextBuilder(db).build(
                actor=actor_account.user,
                space_id=agent_session.space_id,
                agent_kind=agent_session.agent_kind,
                query=latest_text,
                run_id=run.id,
                provider_kind=resolution.kind,
                policy_version=run.policy_version,
            )
            context_build_id = built.build_id
            context_blocks = (
                policy_guard.enforce(policy_guard.context_hook(built.as_data_blocks())) or []
            )
            # ContextBuild/Items are the auditable server-side record for this
            # prefetch.  Persist only after all policy checks succeed.
            db.commit()
    return ContextOut(
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
                id=m.id, role=m.role, content_json=m.content_json, created_at=m.created_at
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


@router.post("/runs/{run_id}/events/append", response_model=EventAppendOut)
def append_events_endpoint(
    run_id: int,
    body: EventAppendRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> EventAppendOut:
    run, _agent_session, _claims = _authorize_run(db, request, run_id)
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
        EventEntry(seq=e.seq, type=e.type, public_payload=e.public_payload) for e in body.events
    ]
    try:
        accepted, duplicates = agent_events.append_events(db, run, entries)
    except FastAPIHTTPException as exc:
        # 部分写入后冲突：回滚半批次，审计后按原错误拒绝（fail-closed）
        db.rollback()
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
    run, _agent_session, _claims = _authorize_run(db, request, run_id)
    policy_guard.enforce(
        policy_guard.agent_settled(status=body.status), code="POLICY_PROVIDER_BLOCKED"
    )
    try:
        settled = agent_queue.settle_run(
            db, run, status=body.status, error_code=body.error_code, error=body.error
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
