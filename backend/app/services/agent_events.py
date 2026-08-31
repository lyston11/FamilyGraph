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
import json
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
from app.models.agent import AgentJob, AgentRun, AgentRunEvent
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
) -> AgentRunEvent:
    """低层插入：供服务内部（入队首个事件 / settle 终态事件）与 append_events 复用。"""
    if event_type not in EVENT_TYPES:
        raise_api_error(422, AGENT_EVENT_INVALID, "未知事件类型", detail={"type": event_type})
    row = AgentRunEvent(
        run_id=run.id,
        seq=seq,
        type=event_type,
        public_payload=public_payload,
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
            # 幂等重放：同 seq 同内容视为重复；不同内容属协议违规
            if prior.type == entry.type and prior.public_payload == entry.public_payload:
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
        accepted.append(
            insert_event(
                db,
                run,
                seq=entry.seq,
                event_type=entry.type,
                public_payload=entry.public_payload,
            )
        )
        if entry.type == "message.assistant_added":
            # Promote the public assistant projection into session history so
            # subsequent Pi turns can restore the full conversation.  Only the
            # bounded text/citation projection is persisted; provider-private
            # payloads never cross this boundary.
            payload = entry.public_payload
            text = payload.get("text")
            if isinstance(text, str):
                session = db.get(AgentRun, run.id)
                if session is not None:
                    from app.models.agent import AgentMessage

                    db.add(
                        AgentMessage(
                            session_id=session.session_id,
                            role="assistant",
                            content_json={
                                "text": text,
                                **(
                                    {"web_citations": payload["web_citations"]}
                                    if isinstance(payload.get("web_citations"), list)
                                    else {}
                                ),
                            },
                            idempotency_key=f"run:{run.id}:event:{entry.seq}",
                            created_at=timeutil.utcnow(),
                        )
                    )
                    db.flush()
        expected_next += 1
        if entry.type == "run.started":
            _promote_to_running(db, run)
    return accepted, duplicates


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
