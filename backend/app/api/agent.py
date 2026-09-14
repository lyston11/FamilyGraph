"""浏览器 Agent API（RT-4/RT-6；前缀 /api/agent，JWT 认证复用现有 deps）。

与 internal 协议（api/internal_agent.py）的信任边界：
- 本路由只服务浏览器用户：JWT 认证、本人会话/Run 可见性（非本人一律 404 防枚举）、
  空间 active 成员校验；不暴露任何内部 token 或密钥材料；
- 浏览器创建的会话固定为 assistant，且无任何 scope 更新端点（DB 层另有
  BEFORE UPDATE trigger 强制不可变）；
- feature flag 关闭时全部端点 503 AGENT_RUNTIME_DISABLED（RT-6 默认整体关闭）。

SSE 合同（design.md / RT-4）：
- 连接即按 seq 升序回放 DB 中该 Run 的全部事件（Last-Event-ID 头或 after_event_id
  查询参数从断点续传，两者都给取较大者），随后尾随新事件直至终态事件后发送并关闭；
- 进程内通知（services.agent_events.notifier）仅作实时性优化：跨进程 append 不在
  本进程注册表内，handler 以可配间隔轮询 DB 兜底；重连回放始终以 DB 为准，
  保证断线恢复无漏序、不乱序，已完成副作用工具不重新执行；
- 心跳为注释行 `:keepalive`（不持久化）；只广播公开事件表的 public_payload，
  敏感 prompt 以外的密钥/未脱敏工具结果不可能进入流（写入侧已 fail-closed 校验）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, Header, Query
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db, require_authenticated_user
from app.db import SessionLocal
from app.errors import (
    AGENT_RUN_ACCOUNT_LIMIT,
    AGENT_RUN_LIMIT,
    AGENT_RUN_NOT_FOUND,
    AGENT_RUN_SESSION_BUSY,
    AGENT_RUNTIME_DISABLED,
    AGENT_SESSION_NOT_FOUND,
    AGENT_SESSION_TITLE_INVALID,
    IDEMPOTENCY_KEY_REQUIRED,
    IDEMPOTENCY_PAYLOAD_CONFLICT,
    PROVIDER_LOCAL_REQUIRED_UNAVAILABLE,
    PROVIDER_UNRESOLVED,
    SPACE_FORBIDDEN_ACTOR,
    SPACE_NOT_FOUND,
    extract_api_error,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import (
    RUN_ACTIVE_STATUSES,
    RUN_TERMINAL_STATUSES,
    AgentMessage,
    AgentRun,
    AgentRunEvent,
    AgentSession,
)
from app.models.space import FamilySpace
from app.models.user import User
from app.schemas.agent import (
    AgentMessageCreatedOut,
    AgentMessageCreateRequest,
    AgentMessageOut,
    AgentRunOut,
    AgentRunRefOut,
    AgentSessionCreateRequest,
    AgentSessionOut,
    AgentSessionRenameRequest,
    CitationOut,
    RunEventCitationsOut,
)
from app.services import (
    agent_citations,
    agent_events,
    agent_provider,
    agent_queue,
    audit,
    policy_guard,
)
from app.services.agent_events import TERMINAL_STREAM_EVENT_TYPES
from app.services.agent_provider import POLICY_ALLOWED, POLICY_DENIED_NO_LOCAL
from app.services.agent_tools import default_allowlist
from app.services.space_fsm import is_active_member
from app.utils import timeutil

_IDEMPOTENCY_CONCURRENCY_CODES = (
    AGENT_RUN_SESSION_BUSY,
    AGENT_RUN_ACCOUNT_LIMIT,
)


def _require_runtime_enabled() -> None:
    """RT-6：Agent 能力由服务端 feature flag 总开关控制，默认整体关闭。"""
    if not config.AGENT_RUNTIME_ENABLED:
        raise_api_error(503, AGENT_RUNTIME_DISABLED, "Agent Runtime 未启用")


router = APIRouter(
    prefix="/agent", tags=["agent"], dependencies=[Depends(_require_runtime_enabled)]
)


# ---- 共享辅助 ----


def _own_session_or_404(db: Session, account_id: int, session_id: int) -> AgentSession:
    agent_session = db.get(AgentSession, session_id)
    if agent_session is None or agent_session.account_id != account_id:
        raise_api_error(404, AGENT_SESSION_NOT_FOUND, "会话不存在")
    return agent_session


def _own_run_or_404(db: Session, account_id: int, run_id: int) -> tuple[AgentRun, AgentSession]:
    """非本人/不存在的 Run 统一 404（防枚举，none→404 语义）。"""
    run = db.get(AgentRun, run_id)
    if run is None:
        raise_api_error(404, AGENT_RUN_NOT_FOUND, "Run 不存在")
    agent_session = db.get(AgentSession, run.session_id)
    if agent_session is None or agent_session.account_id != account_id:
        raise_api_error(404, AGENT_RUN_NOT_FOUND, "Run 不存在")
    return run, agent_session


def _message_citations(
    db: Session, message: AgentMessage, account: Account
) -> tuple[list[dict[str, Any]], int]:
    return agent_citations.project_message_citations(db, message, account)


def _message_out(db: Session, message: AgentMessage, account: Account) -> AgentMessageOut:
    content_json = agent_citations.public_base(message.content_json)
    raw_citations = message.content_json.get("citations")
    projected, unavailable = _message_citations(db, message, account)
    if isinstance(raw_citations, list):
        # Raw stored citations (incl. identifiers of unreadable sources) are
        # never echoed; the projected view replaces them.
        content_json = {k: v for k, v in content_json.items() if k != "citations"}
    return AgentMessageOut(
        id=message.id,
        role=message.role,
        content_json=content_json,
        created_at=message.created_at,
        citations=[CitationOut(**item) for item in projected],
        unavailable_citation_count=unavailable,
    )


# 展示标题长度与前端 truncateSessionTitle 对齐（纯展示，两侧各自实现）。
SESSION_TITLE_DISPLAY_LENGTH = 24


def derive_session_title(text: str) -> str:
    """会话展示标题：折叠空白后截取首 24 字符，超出补省略号（不参与记忆/事实）。"""
    compact = " ".join(text.split())
    chars = list(compact)
    if len(chars) <= SESSION_TITLE_DISPLAY_LENGTH:
        return compact
    return "".join(chars[:SESSION_TITLE_DISPLAY_LENGTH]) + "…"


def _session_out(session: AgentSession) -> AgentSessionOut:
    return AgentSessionOut(
        id=session.id,
        space_id=session.space_id,
        agent_kind=session.agent_kind,
        created_at=session.created_at,
        title=session.title,
        updated_at=session.updated_at,
    )


def _run_ref(run: AgentRun | None) -> AgentRunRefOut | None:
    if run is None:
        return None
    return AgentRunRefOut(
        id=run.id,
        status=run.status,
        attempt=run.attempt,
        cancel_requested=bool(run.cancel_requested),
    )


def _run_out(run: AgentRun) -> AgentRunOut:
    return AgentRunOut(
        id=run.id,
        session_id=run.session_id,
        kind=run.kind,
        status=run.status,
        attempt=run.attempt,
        max_attempts=run.max_attempts,
        cancel_requested=bool(run.cancel_requested),
        error_code=run.error_code,
        created_at=run.created_at,
        updated_at=run.updated_at,
        settled_at=run.settled_at,
    )


def _latest_run_for_message(db: Session, message_id: int) -> AgentRun | None:
    return db.scalar(
        select(AgentRun)
        .where(AgentRun.message_id == message_id)
        .order_by(AgentRun.id.desc())
        .limit(1)
    )


# ---- 会话 ----


@router.post("/sessions", response_model=AgentSessionOut, status_code=201)
def create_agent_session(
    body: AgentSessionCreateRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentSessionOut:
    """创建 Assistant 会话：请求者必须是目标空间 active 成员；scope 创建后不可变。"""
    user, account = identity
    space = db.get(FamilySpace, body.space_id)
    if space is None:
        raise_api_error(404, SPACE_NOT_FOUND, "空间不存在")
    if not is_active_member(db, body.space_id, user.id):
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "仅空间 active 成员可创建 Agent 会话")
    now = timeutil.utcnow()
    row = AgentSession(
        account_id=account.id,
        space_id=body.space_id,
        agent_kind="assistant",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    audit.write_audit(
        db,
        action="agent_session_created",
        actor_id=user.id,
        target_id=row.id,
        detail={"space_id": row.space_id, "agent_kind": row.agent_kind},
    )
    db.commit()
    return _session_out(row)


@router.get("/sessions", response_model=list[AgentSessionOut])
def list_agent_sessions(
    space_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[AgentSessionOut]:
    """列出本人会话，可按 space_id 过滤；按最近活跃排序，limit 封顶。"""
    _user, account = identity
    query = (
        select(AgentSession)
        .where(AgentSession.account_id == account.id)
        .order_by(AgentSession.updated_at.desc(), AgentSession.id.desc())
        .limit(limit)
    )
    if space_id is not None:
        query = query.where(AgentSession.space_id == space_id)
    rows = db.scalars(query).all()
    return [_session_out(r) for r in rows]


@router.patch("/sessions/{session_id}", response_model=AgentSessionOut)
def rename_agent_session(
    session_id: int,
    body: AgentSessionRenameRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentSessionOut:
    """重命名本人会话：标题去空白后须非空（1..120 字符）。"""
    _user, account = identity
    agent_session = _own_session_or_404(db, account.id, session_id)
    title = body.title.strip()
    if not title or len(title) > 120:
        raise_api_error(422, AGENT_SESSION_TITLE_INVALID, "标题须为 1..120 个字符")
    agent_session.title = title
    db.commit()
    return _session_out(agent_session)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_agent_session(
    session_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """删除本人会话：存在非终态 Run 时 409；消息/Run/事件经 FK 级联清理。"""
    user, account = identity
    agent_session = _own_session_or_404(db, account.id, session_id)
    active_run = db.scalar(
        select(AgentRun.id).where(
            AgentRun.session_id == agent_session.id,
            AgentRun.status.in_(RUN_ACTIVE_STATUSES),
        )
    )
    if active_run is not None:
        raise_api_error(409, AGENT_RUN_SESSION_BUSY, "会话有进行中的 Run，请先取消再删除")
    space_id = agent_session.space_id
    agent_kind = agent_session.agent_kind
    db.delete(agent_session)
    db.commit()
    audit.write_audit(
        db,
        action="agent_session_deleted",
        actor_id=user.id,
        target_id=session_id,
        detail={"space_id": space_id, "agent_kind": agent_kind},
    )
    db.commit()
    return Response(status_code=204)


# ---- 消息与幂等 ----


@router.post("/sessions/{session_id}/messages", response_model=AgentMessageCreatedOut)
def create_agent_message(
    session_id: int,
    body: AgentMessageCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentMessageCreatedOut:
    """创建用户消息并入队 Assistant Run（RT-4 Idempotency）。

    - 同 (session, key) 同 payload → 返回原 message+原 run（replayed=true）；
    - 同 key 不同 payload → 409 IDEMPOTENCY_PAYLOAD_CONFLICT；
    - 并发超限 → 409 AGENT_RUN_LIMIT（detail.reason 细分具体限额）；
    - Provider 不可解析 → 可解释拒绝（PROVIDER_*），绝不静默换云。
    """
    user, account = identity
    key = idempotency_key.strip() if idempotency_key else ""
    if not key:
        raise_api_error(400, IDEMPOTENCY_KEY_REQUIRED, "缺少 Idempotency-Key 请求头")
    if len(key) > 120:
        raise_api_error(400, IDEMPOTENCY_KEY_REQUIRED, "Idempotency-Key 过长", {"max_length": 120})
    agent_session = _own_session_or_404(db, account.id, session_id)
    content_json = {"text": body.content}
    policy_guard.enforce(policy_guard.input_hook(body.content))

    # 幂等快路径：命中既有消息直接裁决（不做 Provider 门禁——历史请求原样重放）
    prior = db.scalar(
        select(AgentMessage).where(
            AgentMessage.session_id == agent_session.id,
            AgentMessage.idempotency_key == key,
        )
    )
    if prior is not None:
        if prior.content_json != content_json:
            raise_api_error(
                409,
                IDEMPOTENCY_PAYLOAD_CONFLICT,
                "相同 Idempotency-Key 但请求内容不同",
                {"session_id": agent_session.id},
            )
        return AgentMessageCreatedOut(
            message=_message_out(db, prior, account),
            run=_run_ref(_latest_run_for_message(db, prior.id)),
            replayed=True,
        )

    # 新提交：Provider 策略门禁（可解释拒绝；绝不静默换云）。
    # agent_kind 显式 assistant：浏览器消息链路固定 assistant 维度（steward
    # 消费属子任务 B）；detail.platform_default_configured 供前端区分
    # 「通道未配置」与「空间未选/未同意云」两态文案（design §4）。
    resolution = agent_provider.resolve_for_space(
        db, agent_session.space_id, agent_kind="assistant"
    )
    if resolution.policy_result != POLICY_ALLOWED:
        if resolution.policy_result == POLICY_DENIED_NO_LOCAL:
            raise_api_error(
                409,
                PROVIDER_LOCAL_REQUIRED_UNAVAILABLE,
                "该空间要求本地模型执行，但本地 Provider 不可用",
                {"reason": resolution.reason, "provider_id": resolution.provider_id},
            )
        raise_api_error(
            409,
            PROVIDER_UNRESOLVED,
            "当前空间没有可用的 Provider 配置",
            {
                "policy_result": resolution.policy_result,
                "reason": resolution.reason,
                "platform_default_configured": resolution.platform_default_configured,
            },
        )

    try:
        message, run, replayed = agent_queue.submit_user_message(
            db,
            agent_session=agent_session,
            content_json=content_json,
            idempotency_key=key,
            policy_version=config.AGENT_POLICY_VERSION,
            tool_allowlist=default_allowlist(
                "assistant",
                db,
                account_id=account.id,
                space_id=agent_session.space_id,
            ),
        )
    except FastAPIHTTPException as exc:
        api_error = extract_api_error(exc.detail) or {}
        code = str(api_error.get("code") or "")
        if code in _IDEMPOTENCY_CONCURRENCY_CODES:
            # 浏览器面聚合错误码；reason 保留内部细分（session/account 限额）
            raise_api_error(409, AGENT_RUN_LIMIT, "并发 Run 超限", {"reason": code})
        raise
    if replayed:
        # 并发窗口内先到者已提交：与幂等快路径同构返回
        return AgentMessageCreatedOut(
            message=_message_out(db, message, account), run=_run_ref(run), replayed=True
        )
    # 会话展示态：updated_at 随消息前进；标题只在首条用户消息时派生一次（此后仅重命名可改）。
    agent_session.updated_at = message.created_at
    if agent_session.title is None:
        agent_session.title = derive_session_title(body.content)
    audit.write_audit(
        db,
        action="agent_message_submitted",
        actor_id=user.id,
        target_id=agent_session.id,
        detail={"message_id": message.id, "run_id": run.id if run else None},
    )
    db.commit()
    return AgentMessageCreatedOut(
        message=_message_out(db, message, account), run=_run_ref(run), replayed=False
    )


@router.get("/sessions/{session_id}/messages", response_model=list[AgentMessageOut])
def list_agent_messages(
    session_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[AgentMessageOut]:
    """会话历史投影（不含系统内部字段）。"""
    _user, account = identity
    agent_session = _own_session_or_404(db, account.id, session_id)
    rows = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.session_id == agent_session.id)
        .order_by(AgentMessage.id.asc())
    ).all()
    return [_message_out(db, m, account) for m in rows]


# ---- Run ----


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_agent_run(
    run_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentRunOut:
    _user, account = identity
    run, _agent_session = _own_run_or_404(db, account.id, run_id)
    return _run_out(run)


@router.post("/runs/{run_id}/cancel", response_model=AgentRunOut)
def cancel_agent_run(
    run_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentRunOut:
    """取消 Run（RT-6）：queued→cancelled 立即生效；leased/running 置标记由
    settle 改判（succeeded 结果丢弃）；终态 409。审计记录 actor 归属。"""
    user, account = identity
    run, _agent_session = _own_run_or_404(db, account.id, run_id)
    updated = agent_queue.request_cancel(db, run, actor_id=user.id)
    return _run_out(updated)


# ---- 引用固定后备读取 ----


@router.get("/runs/{run_id}/events/{seq}/citations", response_model=RunEventCitationsOut)
def get_run_event_citations(
    run_id: int,
    seq: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RunEventCitationsOut:
    """按 (run_id, seq) 授权补取完整引用（16 KiB 事件装不下时的固定读取路径）。

    通过服务端生成的消息幂等键定位对应 assistant 消息，重验当前来源权限后
    返回与历史读取相同的投影；不返回摘录，不枚举来源，旧消息返回空集合。
    """
    _user, account = identity
    run, _agent_session = _own_run_or_404(db, account.id, run_id)
    if seq < 0:
        raise_api_error(404, AGENT_RUN_NOT_FOUND, "事件不存在")
    event = db.scalar(
        select(AgentRunEvent).where(
            AgentRunEvent.run_id == run.id,
            AgentRunEvent.seq == seq,
            AgentRunEvent.type == "message.assistant_added",
        )
    )
    if event is None:
        raise_api_error(404, AGENT_RUN_NOT_FOUND, "事件不存在")
    message = agent_citations.event_message(db, run, seq)
    if message is None:
        return RunEventCitationsOut(
            run_id=run.id, seq=seq, citations=[], unavailable_citation_count=0
        )
    projected, unavailable = _message_citations(db, message, account)
    return RunEventCitationsOut(
        run_id=run.id,
        seq=seq,
        citations=[CitationOut(**item) for item in projected],
        unavailable_citation_count=unavailable,
    )


# ---- SSE ----


def _parse_cursor(raw: str | None) -> int | None:
    """Last-Event-ID 解析：非法值忽略（视为未提供），不中断连接建立。"""
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


@router.get("/runs/{run_id}/events")
async def stream_agent_run_events(
    run_id: int,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after_event_id: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> StreamingResponse:
    """Run 公开事件流（text/event-stream）：先回放后尾随，终态后关闭。

    鉴权失败在流开始前以统一错误结构响应（不经流传输）。
    """
    _user, account = identity
    _own_run_or_404(db, account.id, run_id)
    cursors = [c for c in (_parse_cursor(last_event_id), after_event_id) if c is not None]
    cursor = max(cursors) if cursors else -1
    return StreamingResponse(
        _event_stream(run_id, cursor, account.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _fetch_new_events(run_id: int, after_seq: int, account_id: int) -> list[dict[str, Any]] | None:
    """Authorize and project in one short session; only immutable DTOs escape."""
    with SessionLocal() as session:
        account = session.get(Account, account_id)
        run = session.get(AgentRun, run_id)
        agent_session = session.get(AgentSession, run.session_id) if run is not None else None
        if (
            account is None
            or run is None
            or agent_session is None
            or agent_session.account_id != account_id
        ):
            return None
        rows = session.scalars(
            select(AgentRunEvent)
            .where(AgentRunEvent.run_id == run_id, AgentRunEvent.seq > after_seq)
            .order_by(AgentRunEvent.seq.asc())
        ).all()
        return [
            {
                "run_id": row.run_id,
                "seq": row.seq,
                "type": row.type,
                "payload": agent_citations.project_event_payload(
                    session,
                    run,
                    seq=row.seq,
                    event_type=row.type,
                    payload=row.public_payload,
                    account=account,
                ),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]


def _run_is_terminal(run_id: int) -> bool:
    session = SessionLocal()
    try:
        run = session.get(AgentRun, run_id)
        return run is not None and run.status in RUN_TERMINAL_STATUSES
    finally:
        session.close()


def _wire_event(row: dict[str, Any]) -> bytes:
    data = json.dumps(row, ensure_ascii=False)
    return f"id: {row['seq']}\nevent: {row['type']}\ndata: {data}\n\n".encode()


async def _event_stream(run_id: int, cursor: int, account_id: int) -> AsyncIterator[bytes]:
    subscription = agent_events.notifier.subscribe(run_id)
    last_sent = time.monotonic()
    try:
        while True:
            # DB 查询放线程池，避免 SQLite 往返阻塞事件循环
            rows = await anyio.to_thread.run_sync(_fetch_new_events, run_id, cursor, account_id)
            if rows is None:
                return
            for row in rows:
                yield _wire_event(row)
                cursor = row["seq"]
                last_sent = time.monotonic()
                if row["type"] in TERMINAL_STREAM_EVENT_TYPES:
                    return
            if await anyio.to_thread.run_sync(_run_is_terminal, run_id):
                # 终态但终态事件缺失（如 reaper 直接收敛）：按状态收口关闭
                return
            try:
                await asyncio.wait_for(
                    subscription.queue.get(), timeout=config.AGENT_SSE_POLL_SECONDS
                )
            except TimeoutError:
                pass  # 无通知：进入下一轮轮询兜底（跨进程 append 场景的唯一来源）
            if time.monotonic() - last_sent >= config.AGENT_SSE_KEEPALIVE_SECONDS:
                yield b":keepalive\n\n"
                last_sent = time.monotonic()
    finally:
        agent_events.notifier.unsubscribe(run_id, subscription)
