"""Agent Run 公开事件流（RT-4：先持久化再广播；每 Run 单调 seq）。

事件类型注册表为 notes.md 首版枚举；card.* 为 V2.4 预留命名空间，当前一律拒绝。
追加合同：
- seq 由调用方（sidecar/服务内部）显式提供，新事件必须严格等于 max(seq)+1；
- 完全相同的 (seq, type, payload) 重试 → 幂等，返回 duplicates；
- 同 seq 不同内容 / 空洞 / 回退 → AGENT_EVENT_SEQ_CONFLICT（fail-closed）；
- 未知类型或非法 payload 拒绝且不落公开流，由 API 层写安全审计。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import (
    AGENT_EVENT_INVALID,
    AGENT_EVENT_SEQ_CONFLICT,
    AGENT_RUN_NOT_RUNNING,
    raise_api_error,
)
from app.models.agent import AgentJob, AgentRun, AgentRunEvent, AgentSession
from app.models.rag import RAGDocument
from app.utils import timeutil

# notes.md 事件类型注册表（首版）
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run.started",
        "message.user_added",
        "turn.started",
        "turn.completed",
        "message.assistant_added",
        "tool.execution.started",
        "tool.execution.completed",
        "run.settled",
        "run.failed",
        "run.cancelled",
        "run.expired",
    }
)

# settle 落终态时自动追加的对应事件（queue.settle 消费）
TERMINAL_EVENT_FOR: dict[str, str] = {
    "succeeded": "run.settled",
    "failed": "run.failed",
    "cancelled": "run.cancelled",
    "expired": "run.expired",
}

# 终态事件类型集合：SSE 流发送后即关闭（design.md）
TERMINAL_STREAM_EVENT_TYPES: frozenset[str] = frozenset(TERMINAL_EVENT_FOR.values())

MAX_PAYLOAD_BYTES = 16 * 1024


@dataclass(frozen=True)
class EventEntry:
    """一次追加的输入单元：seq 显式、type 注册表内、payload 必须是 JSON object。"""

    seq: int
    type: str
    public_payload: dict[str, Any]

    @property
    def request_fingerprint(self) -> str:
        """规范化原始请求指纹（type + 首次收到的候选 payload，与认证结果无关）。"""
        canonical = json.dumps(
            {"type": self.type, "public_payload": self.public_payload},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def next_seq(db: Session, run_id: int) -> int:
    """当前最大 seq + 1；空流从 0 开始。"""
    current = db.scalar(
        select(AgentRunEvent.seq)
        .where(AgentRunEvent.run_id == run_id)
        .order_by(AgentRunEvent.seq.desc())
        .limit(1)
    )
    return 0 if current is None else int(current) + 1


def insert_event(
    db: Session,
    run: AgentRun,
    *,
    seq: int,
    event_type: str,
    public_payload: dict[str, Any],
    created_at: datetime | None = None,
    request_fingerprint: str | None = None,
    context_reference: dict[str, Any] | None = None,
) -> AgentRunEvent:
    """低层插入：供服务内部（入队首个事件 / settle 终态事件）与 append_events 复用。"""
    if event_type not in EVENT_TYPES:
        raise_api_error(422, AGENT_EVENT_INVALID, "未知事件类型", detail={"type": event_type})
    row = AgentRunEvent(
        run_id=run.id,
        seq=seq,
        type=event_type,
        public_payload=public_payload,
        request_fingerprint=request_fingerprint,
        context_reference_json=context_reference,
        created_at=created_at or timeutil.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _validate_entry(entry: EventEntry) -> None:
    if entry.type not in EVENT_TYPES:
        raise_api_error(422, AGENT_EVENT_INVALID, "未知事件类型", detail={"type": entry.type})
    if not isinstance(entry.public_payload, dict):
        raise_api_error(422, AGENT_EVENT_INVALID, "payload 必须为 JSON object")
    try:
        size = len(json.dumps(entry.public_payload, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise_api_error(422, AGENT_EVENT_INVALID, "payload 不可序列化")
    if size > MAX_PAYLOAD_BYTES:
        raise_api_error(422, AGENT_EVENT_INVALID, "payload 超限")


def append_events(
    db: Session,
    run: AgentRun,
    entries: list[EventEntry],
) -> tuple[list[AgentRunEvent], list[int]]:
    """幂等批量追加。

    返回 (accepted_rows, duplicate_seqs)；冲突/非法直接抛错（不落任何一行，
    由调用方事务决定回滚范围）。run.started 追加成功时将 leased 提升为 running。
    新事件必须严格接在当前流末尾（seq == max+1，含本批次先前条目），
    保证 SSE 重放无漏序、乱序。
    """
    accepted: list[AgentRunEvent] = []
    duplicates: list[int] = []
    expected_next = next_seq(db, run.id)
    for entry in entries:
        _validate_entry(entry)
        prior: AgentRunEvent | None = db.scalar(
            select(AgentRunEvent).where(
                AgentRunEvent.run_id == run.id, AgentRunEvent.seq == entry.seq
            )
        )
        if prior is not None:
            # 幂等重放：同 seq 且同原始请求指纹（旧事件无指纹时退化为 payload
            # 全等）视为重复；指纹不比较服务端认证后的结果，异指纹同 seq 属
            # 协议违规。
            prior_fingerprint = prior.request_fingerprint
            if prior_fingerprint is not None:
                same = prior_fingerprint == entry.request_fingerprint
            else:
                same = prior.type == entry.type and prior.public_payload == entry.public_payload
            if same:
                duplicates.append(entry.seq)
                continue
            raise_api_error(
                409,
                AGENT_EVENT_SEQ_CONFLICT,
                "事件序号已存在且内容不一致",
                detail={"seq": entry.seq},
            )
        if entry.seq != expected_next:
            raise_api_error(
                409,
                AGENT_EVENT_SEQ_CONFLICT,
                "事件序号必须严格递增且连续",
                detail={"seq": entry.seq, "expected": expected_next},
            )
        if entry.type == "run.started" and run.status == "queued":
            # 预检：queued 直接发 run.started 属协议违规，先于插入拒绝（不落公开流）
            raise_api_error(
                409,
                AGENT_RUN_NOT_RUNNING,
                "仅 leased 状态可开始执行",
                detail={"status": run.status},
            )
        # Citation authentication happens for not-yet-committed events only;
        # replayed duplicates above never re-generate persistent payloads.
        context_reference: dict[str, Any] | None = None
        authenticated_citations: list[dict[str, Any]] | None = None
        if entry.type == "message.assistant_added" and isinstance(
            entry.public_payload.get("text"), str
        ):
            context_reference, authenticated_citations = _authenticate_citations(db, run, entry)
        accepted.append(
            insert_event(
                db,
                run,
                seq=entry.seq,
                event_type=entry.type,
                public_payload=entry.public_payload,
                request_fingerprint=entry.request_fingerprint,
                context_reference=context_reference,
            )
        )
        if entry.type == "message.assistant_added":
            # Promote the public assistant projection into session history so
            # subsequent Pi turns can restore the full conversation.  Only the
            # bounded text/citation projection is persisted; provider-private
            # payloads never cross this boundary.  Memory citations are
            # authenticated server-side from the attempt's context build —
            # the model text alone never turns into a verified citation.
            payload = entry.public_payload
            text = payload.get("text")
            if isinstance(text, str):
                session = db.get(AgentRun, run.id)
                if session is not None:
                    from app.models.agent import AgentMessage

                    content_json: dict[str, Any] = {"text": text}
                    if authenticated_citations:
                        content_json["citations"] = authenticated_citations
                    if isinstance(payload.get("web_citations"), list):
                        content_json["web_citations"] = payload["web_citations"]
                    db.add(
                        AgentMessage(
                            session_id=session.session_id,
                            role="assistant",
                            content_json=content_json,
                            idempotency_key=f"run:{run.id}:event:{entry.seq}",
                            created_at=timeutil.utcnow(),
                        )
                    )
                    db.flush()
        expected_next += 1
        if entry.type == "run.started":
            _promote_to_running(db, run)
    return accepted, duplicates


# Citation handles look like ``rag:<source_id>:r<rev>:c<chunk>``.
_CITATION_HANDLE_RE = re.compile(r"rag:[^\s:]+:r\d+:c\d+")
_MAX_USED_HANDLES = 32


def _revision_from_handle(citation_handle: str) -> int | None:
    segment = citation_handle.split(":r", 1)[1] if ":r" in citation_handle else ""
    segment = segment.split(":", 1)[0]
    try:
        return int(segment)
    except ValueError:
        return None


def _authenticate_citations(
    db: Session, run: AgentRun, entry: EventEntry
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    """Authenticate the citation handles the answer actually used.

    A handle becomes a verified citation only when it belongs to an included
    item of this run/attempt's context build and the source is still readable
    for the acting account.  Fabricated, other-build, revoked or
    wrong-revision handles stay unauthenticated: the text remains unverified
    text, never a confirmed fact.  Returns (context_reference, citations).
    """
    from app.models.account import Account
    from app.models.context import ContextBuild, ContextBuildItem
    from app.services import memory_sources

    text_value = entry.public_payload.get("text")
    if not isinstance(text_value, str):
        return None, None
    current_run = db.get(AgentRun, run.id)
    if current_run is None:
        return None, None
    run = current_run
    build = db.scalar(
        select(ContextBuild)
        .where(ContextBuild.run_id == run.id, ContextBuild.attempt == run.attempt)
        .order_by(ContextBuild.id.desc())
        .limit(1)
    )
    if build is None:
        return None, None
    handles: list[str] = []
    seen: set[str] = set()
    for match in _CITATION_HANDLE_RE.findall(text_value):
        if match not in seen:
            seen.add(match)
            handles.append(match)
        if len(handles) >= _MAX_USED_HANDLES:
            break
    if not handles:
        return {"context_build_id": build.id, "attempt": run.attempt, "used_handles": []}, None
    agent_session = db.get(AgentSession, run.session_id)
    account = db.get(Account, agent_session.account_id) if agent_session is not None else None
    actor = account.user if account is not None else None
    if actor is None or account is None or agent_session is None:
        return {"context_build_id": build.id, "attempt": run.attempt, "used_handles": []}, None
    items = {
        item.citation_handle: item
        for item in db.scalars(
            select(ContextBuildItem).where(
                ContextBuildItem.build_id == build.id,
                ContextBuildItem.included.is_(True),
            )
        ).all()
    }
    citations: list[dict[str, Any]] = []
    used_handles: list[str] = []
    for handle in handles:
        item = items.get(handle)
        if item is None:
            continue
        document = db.scalar(
            select(RAGDocument).where(
                RAGDocument.source_type == item.source_type,
                RAGDocument.source_id == item.source_id,
                RAGDocument.status == "active",
            )
        )
        handle_revision = _revision_from_handle(handle)
        if (
            document is None
            or (handle_revision is not None and document.revision != handle_revision)
            or not memory_sources.document_readable(
                db,
                document,
                actor=actor,
                account=account,
                space_id=agent_session.space_id,
                agent_kind=run.kind,
            )
        ):
            continue
        used_handles.append(handle)
        citations.append(
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "scope": str(item.metadata_json.get("scope", document.scope)),
                "sensitivity": str(item.metadata_json.get("sensitivity", document.sensitivity)),
                "revision": int(document.revision),
                "citation_handle": handle,
            }
        )
    context_reference = {
        "context_build_id": build.id,
        "attempt": run.attempt,
        "used_handles": used_handles,
    }
    return context_reference, citations


def _promote_to_running(db: Session, run: AgentRun) -> None:
    """run.started 持久化后 leased → running（design.md FSM：worker starts + heartbeat）。"""
    if run.status == "running":
        return
    if run.status != "leased":
        raise_api_error(
            409,
            AGENT_RUN_NOT_RUNNING,
            "仅 leased 状态可开始执行",
            detail={"status": run.status},
        )
    now = timeutil.utcnow()
    run.status = "running"
    run.updated_at = now
    job = db.get(AgentJob, run.job_id) if run.job_id is not None else None
    if job is not None:
        job.status = "running"
        job.updated_at = now


# ---- 进程内广播注册表（SSE 实时性优化；正确性始终以 DB 持久化 + 回放兜底）----


@dataclass(frozen=True)
class EventSubscription:
    """一次 SSE 订阅：绑定订阅时的运行 loop 与容量为 1 的信号队列。"""

    queue: asyncio.Queue[None]
    loop: asyncio.AbstractEventLoop


def _signal(subscription: EventSubscription) -> None:
    try:
        subscription.queue.put_nowait(None)
    except asyncio.QueueFull:
        pass  # 已有未消费信号，唤醒效果等同


class RunEventNotifier:
    """进程内 run 事件通知注册表（design.md：事务插入后发布进程内通知）。

    publish 由同步服务层在提交成功后调用，可能位于工作线程：经
    loop.call_soon_threadsafe 投递，线程安全。跨进程部署时其他进程的追加
    不在本注册表内——SSE handler 以可配间隔轮询 DB 兑底，重连回放始终从
    DB 读取，保证不漏序、不乱序（注释合同见 api/agent.py SSE 段）。
    """

    def __init__(self) -> None:
        self._subs: dict[int, set[EventSubscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, run_id: int) -> EventSubscription:
        subscription = EventSubscription(
            queue=asyncio.Queue(maxsize=1), loop=asyncio.get_running_loop()
        )
        with self._lock:
            self._subs.setdefault(run_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, run_id: int, subscription: EventSubscription) -> None:
        with self._lock:
            bucket = self._subs.get(run_id)
            if bucket is not None:
                bucket.discard(subscription)
                if not bucket:
                    del self._subs[run_id]

    def publish(self, run_id: int) -> None:
        """唤醒该 run 的全部本进程订阅者（无订阅者时为空操作）。"""
        with self._lock:
            subscribers = tuple(self._subs.get(run_id, ()))
        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(_signal, subscription)
            except RuntimeError:
                pass  # 订阅方 loop 已关闭：连接随之结束，忽略


notifier = RunEventNotifier()
