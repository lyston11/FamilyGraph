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

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import (
    AGENT_EVENT_INVALID,
    AGENT_EVENT_SEQ_CONFLICT,
    AGENT_RUN_NOT_RUNNING,
    raise_api_error,
)
from app.models.agent import AgentJob, AgentRun, AgentRunEvent, AgentSession
from app.schemas.agent import ContextReferenceIn, EventTimingIn
from app.services import agent_citations
from app.services.agent_execution import ExecutionIdentity, acquire_run_writer, fence_execution
from app.utils import timeutil

# notes.md 事件类型注册表（V2.2）
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run.started",
        "message.user_added",
        "turn.started",
        "turn.completed",
        "message.assistant_added",
        "assistant.text_delta",
        "assistant.text_reset",
        "tool.execution.started",
        "tool.execution.completed",
        "run.compacted",
        "run.settled",
        "run.failed",
        "run.cancelled",
        "run.expired",
    }
)

# 临时正文显示事件（09-18 P0-2）：只是显示投影，永不物化 AgentMessage。
# 它们不进历史/Memory/RAG，也不带引用或权限声明——读时授权由 SSE/回放端点的
# 账号归属复核承担。只有 message.assistant_added（message_end）是权威结果。
PROVISIONAL_TEXT_EVENT_TYPES: frozenset[str] = frozenset(
    {"assistant.text_delta", "assistant.text_reset"}
)

# 单条临时正文分片的上限（码点）。sidecar 按 MAX_PROSE_FRAGMENT_CHARS 分片，
# 这里再夹一次：畸形或过大的分片会被拒绝，而不是让整批 append 一起失败。
MAX_PROVISIONAL_DELTA_CHARS = 4000

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
    context_reference: dict[str, Any] | None = None
    # 有界内部计时（09-17 D）：只含 source/duration_ms，永不进 public_payload。
    timing: dict[str, Any] | None = None

    def fingerprint(self, run_id: int, attempt: int) -> str:
        canonical = json.dumps(
            {
                "v": 2,
                "run_id": run_id,
                "attempt": attempt,
                "seq": self.seq,
                "type": self.type,
                "public_payload": self.public_payload,
                "context_reference": self.context_reference,
                "timing": self.timing,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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
    timing: dict[str, Any] | None = None,
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
        timing_json=timing,
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
    if entry.type in PROVISIONAL_TEXT_EVENT_TYPES:
        # 形状在这里收一次口：临时正文是只读显示投影，字段集闭合，
        # 否则一个畸形分片会被原样广播给读者。
        _validate_provisional_payload(entry)
    try:
        size = len(
            json.dumps(entry.public_payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError):
        raise_api_error(422, AGENT_EVENT_INVALID, "payload 不可序列化")
    if size > MAX_PAYLOAD_BYTES:
        raise_api_error(422, AGENT_EVENT_INVALID, "payload 超限")
    if entry.context_reference is not None:
        try:
            ref = ContextReferenceIn.model_validate(entry.context_reference)
        except ValidationError:
            raise_api_error(422, AGENT_EVENT_INVALID, "context_reference 格式无效")
        if entry.type != "message.assistant_added" or len(set(ref.used_handles)) != len(
            ref.used_handles
        ):
            raise_api_error(422, AGENT_EVENT_INVALID, "context_reference 使用无效")
    if entry.timing is not None:
        # 与 internal schema 同源校验：形状/上下界/子成分一致性一次收紧，
        # 避免服务层与 API 层两套判定漂移。
        try:
            EventTimingIn.model_validate(entry.timing)
        except ValidationError:
            raise_api_error(422, AGENT_EVENT_INVALID, "timing 格式无效")


def _validate_provisional_payload(entry: EventEntry) -> None:
    """临时正文事件的闭合形状校验（fail-closed）。

    只有 ``role=assistant`` 与（``text_delta`` 的）非空 ``delta`` 两个字段；
    额外键拒绝，避免 sidecar 侧协议漂移把内部字段带进公共事件流。
    """
    payload = entry.public_payload
    if set(payload) - {"role", "delta"}:
        raise_api_error(422, AGENT_EVENT_INVALID, "临时正文事件含未允许字段")
    if payload.get("role") != "assistant":
        raise_api_error(422, AGENT_EVENT_INVALID, "临时正文事件 role 必须为 assistant")
    if entry.type == "assistant.text_delta":
        delta = payload.get("delta")
        if not isinstance(delta, str) or delta == "":
            raise_api_error(422, AGENT_EVENT_INVALID, "text_delta 必须携带非空 delta")
        if len(delta) > MAX_PROVISIONAL_DELTA_CHARS:
            raise_api_error(
                422,
                AGENT_EVENT_INVALID,
                "临时正文分片超限",
                detail={"chars": len(delta)},
            )
    elif "delta" in payload:
        raise_api_error(422, AGENT_EVENT_INVALID, "text_reset 不得携带 delta")


def append_events(
    db: Session,
    run: AgentRun,
    entries: list[EventEntry],
    *,
    execution: ExecutionIdentity | None = None,
) -> tuple[list[AgentRunEvent], list[int]]:
    """幂等批量追加。

    返回 (accepted_rows, duplicate_seqs)；冲突/非法直接抛错（不落任何一行，
    由调用方事务决定回滚范围）。run.started 追加成功时将 leased 提升为 running。
    新事件必须严格接在当前流末尾（seq == max+1，含本批次先前条目），
    保证 SSE 重放无漏序、乱序。
    """
    if execution is not None:
        run, _session, _job = fence_execution(db, execution)
    else:
        acquire_run_writer(db, run.id)
    expected_attempt = execution.expected_attempt if execution is not None else run.attempt
    accepted: list[AgentRunEvent] = []
    duplicates: list[int] = []
    expected_next = next_seq(db, run.id)
    for entry in entries:
        _validate_entry(entry)
        fingerprint = entry.fingerprint(run.id, expected_attempt)
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
            if (prior.context_reference_json or {}).get("protocol_version") == 2:
                same = prior.type == entry.type and prior_fingerprint == fingerprint
            elif prior_fingerprint is not None:
                same = (
                    entry.context_reference is None
                    and prior.type == entry.type
                    and prior_fingerprint == entry.request_fingerprint
                )
            else:
                same = (
                    entry.context_reference is None
                    and prior.type == entry.type
                    and prior.public_payload == entry.public_payload
                )
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
        context_record: dict[str, Any] = {
            "protocol_version": 2,
            "attempt": expected_attempt,
            "submitted": entry.context_reference,
        }
        authenticated: list[dict[str, Any]] = []
        unavailable = 0
        payload = agent_citations.public_base(entry.public_payload)
        if entry.type == "message.assistant_added" and isinstance(payload.get("text"), str):
            authenticated, unavailable = _authenticate_citations(db, run, entry, expected_attempt)
            from app.models.account import Account
            from app.models.agent import AgentMessage

            content_json: dict[str, Any] = {"text": payload["text"]}
            if authenticated:
                content_json["citations"] = authenticated
            if unavailable:
                content_json["unavailable_citation_count"] = unavailable
            if isinstance(payload.get("web_citations"), list):
                content_json["web_citations"] = payload["web_citations"]
            message = AgentMessage(
                session_id=run.session_id,
                role="assistant",
                content_json=content_json,
                idempotency_key=f"run:{run.id}:event:{entry.seq}",
                created_at=timeutil.utcnow(),
            )
            db.add(message)
            db.flush()
            session = db.get(AgentSession, run.session_id)
            account = db.get(Account, session.account_id) if session else None
            if account is not None:
                projected, unavailable = agent_citations.project_message_citations(
                    db, message, account
                )
                payload = agent_citations.fit_public_citations(payload, projected, unavailable)
        accepted.append(
            insert_event(
                db,
                run,
                seq=entry.seq,
                event_type=entry.type,
                public_payload=payload,
                request_fingerprint=fingerprint,
                context_reference=context_record,
                timing=entry.timing,
            )
        )
        expected_next += 1
        if entry.type == "run.started":
            _promote_to_running(db, run)
    return accepted, duplicates


def _authenticate_citations(
    db: Session,
    run: AgentRun,
    entry: EventEntry,
    expected_attempt: int,
) -> tuple[list[dict[str, Any]], int]:
    """Wire references locate server evidence; text alone never authenticates."""
    from app.models.account import Account
    from app.models.context import ContextBuild, ContextBuildItem
    from app.models.platform_features import PlatformFeatureConfig
    from app.services import agent_provider, memory_sources, platform_features
    from app.services.context_builder import invalidate_build

    ref = entry.context_reference
    if ref is None:
        return [], 0  # Legacy sidecars retain text/web, not guessed citations.
    build = db.get(ContextBuild, ref["build_id"], populate_existing=True)
    session = db.get(AgentSession, run.session_id)
    if (
        build is None
        or session is None
        or ref["attempt"] != expected_attempt
        or build.run_id != run.id
        or build.attempt != expected_attempt
        or build.account_id != session.account_id
        or build.space_id != session.space_id
        or build.agent_kind != run.kind
    ):
        raise_api_error(409, AGENT_EVENT_INVALID, "context_reference 不属于当前执行")
    items = list(
        db.scalars(
            select(ContextBuildItem)
            .where(ContextBuildItem.build_id == build.id, ContextBuildItem.included.is_(True))
            .order_by(ContextBuildItem.rank, ContextBuildItem.id)
        )
    )
    handles = ref["used_handles"]
    mentioned = set(
        re.findall(r"\[(rag:[^\s\]]{1,250})\]", str(entry.public_payload.get("text", "")))
    )
    if any(
        sum(item.citation_handle == h for item in items) != 1 or h not in mentioned for h in handles
    ):
        raise_api_error(409, AGENT_EVENT_INVALID, "句柄未纳入此构建或未出现在回答中")
    account = db.get(Account, session.account_id)
    if account is None:
        return [], len(handles)
    db.get(PlatformFeatureConfig, 1, populate_existing=True)
    resolution = agent_provider.resolve_for_run(db, run, session.space_id)
    decision = {
        "provider_id": resolution.provider_id,
        "model": resolution.model,
        "policy_result": resolution.policy_result,
    }
    stored_policy = build.policy_json or {}
    from app import config

    if (
        build.invalidated_at is not None
        or (stored_policy.get("rag_enabled") and not platform_features.is_rag_enabled(db))
        or stored_policy.get("provider_kind") != resolution.kind
        or stored_policy.get("provider_decision") != decision
        or stored_policy.get("deployment_policy_version") != config.POLICY_VERSION
    ):
        invalidate_build(db, build, "policy_changed")
        return [], len(handles)
    citations: list[dict[str, Any]] = []
    unavailable = 0
    for item in items:
        if item.citation_handle not in handles:
            continue
        source_ref = memory_sources.ExactChunkRef.parse(item.metadata_json.get("source_ref"))
        resolved = (
            memory_sources.read_exact_chunk(
                db,
                source_ref,
                actor=account.user,
                account=account,
                space_id=session.space_id,
                agent_kind=run.kind,
                for_model=True,
                provider_kind=(build.policy_json or {}).get("provider_kind"),
            )
            if source_ref
            else None
        )
        if (
            source_ref is None
            or resolved is None
            or source_ref.source_type != item.source_type
            or source_ref.source_id != item.source_id
            or item.metadata_json.get("revision") != source_ref.source_revision
            or item.citation_handle
            != f"rag:{source_ref.source_id}:r{source_ref.source_revision}:c{source_ref.chunk_id}"
        ):
            unavailable += 1
            invalidate_build(db, build, "source_changed")
            continue
        citations.append(
            {
                "source_type": source_ref.source_type,
                "source_id": source_ref.source_id,
                "revision": source_ref.source_revision,
                "scope": resolved.document.scope,
                "sensitivity": resolved.document.sensitivity,
                "citation_handle": item.citation_handle,
                "_source_ref": source_ref.as_json(),
            }
        )
    return citations, unavailable


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
