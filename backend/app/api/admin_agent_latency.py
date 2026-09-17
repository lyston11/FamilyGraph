"""Agent 链路延迟观测（09-13-agent-latency-tuning；仅 admin_app :8002，只读）。

口径合同（由现有 schema 决定，避免伪精度）：
- ``steward_model_calls.latency_ms`` 是每次模型调用真实耗时；``error_code=timeout``
  的调用在 ``STEWARD_ASSIST_TIMEOUT_SECONDS`` 处被截断（删失样本），分位数对
  含删失样本的全体计算，超时单列计数——高超时占比时 p50/p95 会偏小，解读需结合
  timeout 计数；
- ``agent_runs`` 的 ``lease_expires_at`` 被心跳持续前移，**不能**用它反推被租走时刻；
- 但 ``agent_run_events`` 每行都有 ``created_at``，且 ``run.started`` 正是在
  lease→running 转换点落库（``agent_events._promote_to_running``），因此
  「入队→取得执行权」「取得执行权→首次正文」「每轮模型生成」「工具执行」
  「最后事件→结算」都可由既有持久事件精确分解，**不需要新增字段**。
  分段一律以事件 ``created_at`` 为真源，不用续租字段倒推。
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminPrincipal, require_admin_ready
from app.api.deps import get_db
from app.models.agent import AgentRun, AgentRunEvent
from app.models.steward import StewardModelCall
from app.services import admin_audit
from app.utils.timeutil import utcnow

router = APIRouter(prefix="/admin-api/v1", tags=["admin-agent-latency"])
_ENDPOINT = "/admin-api/v1/agent/latency"

_OBSERVATION_NOTES: tuple[str, ...] = (
    "steward assist 分位数包含 error_code=timeout 的删失样本（在超时预算处截断），"
    "高 timeout 占比时 p50/p95 偏小；timeout 计数单列给出",
    "assistant 分段来自 agent_run_events.created_at（run.started 即 lease→running 落库点）；"
    "lease_expires_at 被心跳续期，不参与任何分段计算",
    "first_text 每 run 一个样本（取得执行权→首个 assistant 正文）；model_turn 每轮一个"
    "样本（turn.started→该轮 assistant 正文），多轮 run 会贡献多个样本，两者不可混算",
    "tool_call 按 tool_call_id 配对；未配对（运行中/中断）的调用不计入样本",
    "run 在取得执行权前结束（queued 取消等）无 run.started，单列 runs_without_start",
)


class AssistKindLatency(BaseModel):
    total: int
    succeeded: int
    timeout: int
    other: int
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    latency_max_ms: int | None = None


class RunTotalLatency(BaseModel):
    n: int
    total_p50_s: float | None = None
    total_p95_s: float | None = None
    total_max_s: float | None = None


class PhaseStats(BaseModel):
    """单阶段耗时分布（毫秒）；空样本 n=0 且分位为 null，不零填充。"""

    n: int
    p50_ms: int | None = None
    p95_ms: int | None = None
    max_ms: int | None = None


class RunPhaseBreakdown(BaseModel):
    """assistant run 分段：全部由持久事件时间戳推导，无新增字段。"""

    runs: int
    runs_without_start: int
    queue_wait: PhaseStats
    first_text: PhaseStats
    model_turn: PhaseStats
    tool_call: PhaseStats
    settle: PhaseStats


class AgentLatencyOut(BaseModel):
    generated_at: datetime
    window_days: int
    steward_assist: dict[str, AssistKindLatency]
    assistant_runs: RunTotalLatency
    assistant_phases: RunPhaseBreakdown
    runs_by_status: dict[str, int]
    notes: list[str]


def _nearest_rank(sorted_values: list[int], quantile: float) -> int | None:
    """nearest-rank 分位数：ceil(q*n) 名；空样本返回 None。"""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(quantile * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _assist_stats(db: Session, cutoff: datetime) -> dict[str, AssistKindLatency]:
    rows = db.execute(
        select(
            StewardModelCall.assist_kind,
            StewardModelCall.status,
            StewardModelCall.error_code,
            StewardModelCall.latency_ms,
        ).where(StewardModelCall.created_at >= cutoff)
    ).all()
    grouped: dict[str, list[tuple[str, str | None, int | None]]] = {}
    for kind, status, error_code, latency_ms in rows:
        grouped.setdefault(kind, []).append((status, error_code, latency_ms))

    stats: dict[str, AssistKindLatency] = {}
    for kind in sorted(grouped):
        kind_rows = grouped[kind]
        latencies = sorted(r[2] for r in kind_rows if r[2] is not None)
        succeeded = sum(1 for r in kind_rows if r[0] == "succeeded")
        timeout = sum(1 for r in kind_rows if (r[1] or "") == "timeout")
        stats[kind] = AssistKindLatency(
            total=len(kind_rows),
            succeeded=succeeded,
            timeout=timeout,
            other=len(kind_rows) - succeeded - timeout,
            latency_p50_ms=_nearest_rank(latencies, 0.50),
            latency_p95_ms=_nearest_rank(latencies, 0.95),
            latency_max_ms=latencies[-1] if latencies else None,
        )
    return stats


def _run_stats(db: Session, cutoff: datetime) -> tuple[RunTotalLatency, dict[str, int]]:
    rows = db.execute(
        select(AgentRun.status, AgentRun.created_at, AgentRun.settled_at).where(
            AgentRun.created_at >= cutoff
        )
    ).all()
    by_status = Counter(status for status, _created, _settled in rows)
    totals = sorted(
        int((settled - created).total_seconds())
        for _status, created, settled in rows
        if settled is not None
    )
    stats = RunTotalLatency(
        n=len(totals),
        total_p50_s=_nearest_rank(totals, 0.50),
        total_p95_s=_nearest_rank(totals, 0.95),
        total_max_s=totals[-1] if totals else None,
    )
    return stats, dict(sorted(by_status.items()))


def _ms(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() * 1000))


def _phase_stats(values: list[int]) -> PhaseStats:
    ordered = sorted(values)
    return PhaseStats(
        n=len(ordered),
        p50_ms=_nearest_rank(ordered, 0.50),
        p95_ms=_nearest_rank(ordered, 0.95),
        max_ms=ordered[-1] if ordered else None,
    )


def _phase_breakdown(db: Session, cutoff: datetime) -> RunPhaseBreakdown:
    """按 run 分解 assistant 耗时；只用已持久事件，缺样本即 n=0。"""
    runs = db.execute(
        select(AgentRun.id, AgentRun.created_at, AgentRun.settled_at).where(
            AgentRun.created_at >= cutoff
        )
    ).all()
    created_at = {run_id: created for run_id, created, _ in runs}
    settled_at = {run_id: settled for run_id, _, settled in runs}

    rows = db.execute(
        select(
            AgentRunEvent.run_id,
            AgentRunEvent.type,
            AgentRunEvent.created_at,
            AgentRunEvent.public_payload,
        )
        .join(AgentRun, AgentRun.id == AgentRunEvent.run_id)
        .where(AgentRun.created_at >= cutoff)
        .order_by(AgentRunEvent.run_id, AgentRunEvent.seq)
    ).all()

    per_run: dict[int, list[tuple[str, datetime, dict[str, Any]]]] = {}
    for run_id, event_type, at, payload in rows:
        per_run.setdefault(run_id, []).append((event_type, at, payload or {}))

    queue_wait: list[int] = []
    first_text: list[int] = []
    model_turn: list[int] = []
    tool_call: list[int] = []
    settle: list[int] = []
    runs_without_start = 0

    for run_id, events in per_run.items():
        started = next((at for kind, at, _ in events if kind == "run.started"), None)
        created = created_at.get(run_id)
        if started is None:
            # 取得执行权前就结束（queued 取消等）：无 run.started，不伪造分段。
            runs_without_start += 1
        elif created is not None:
            queue_wait.append(_ms(started - created))

        if started is not None:
            first = next(
                (at for kind, at, _ in events if kind == "message.assistant_added"),
                None,
            )
            if first is not None:
                first_text.append(_ms(first - started))

        # 每轮模型生成 = 该轮 turn.started → 该轮内首个 assistant 正文。
        # 只在本轮内查找（下一个 turn.started 之前），因此无正文的工具轮
        # 不会把下一轮的正文误算进自己；无正文即无样本。
        # 多轮 run 贡献多个样本，与 per-run 的 first_text 分开统计。
        for index, (kind, at, _) in enumerate(events):
            if kind != "turn.started":
                continue
            produced: datetime | None = None
            for later_kind, later, _ in events[index + 1 :]:
                if later_kind == "turn.started":
                    break
                if later_kind == "message.assistant_added":
                    produced = later
                    break
            if produced is not None:
                model_turn.append(_ms(produced - at))

        open_tools: dict[str, datetime] = {}
        for kind, at, payload in events:
            if kind not in ("tool.execution.started", "tool.execution.completed"):
                continue
            key = str(payload.get("tool_call_id", ""))
            if kind == "tool.execution.started":
                open_tools[key] = at
            else:
                opened = open_tools.pop(key, None)
                if opened is not None:
                    tool_call.append(_ms(at - opened))

        # 结算开销 = 最后一个非终态事件（正文/工具落库）→ run 终态，
        # 即事件 flush 与 settle 写入的尾部成本；不是拿终态事件和自身相减。
        settled = settled_at.get(run_id)
        if settled is not None:
            terminal = {"run.settled", "run.failed", "run.cancelled", "run.expired"}
            tail = [at for kind, at, _ in events if kind not in terminal]
            if tail:
                settle.append(_ms(settled - max(tail)))

    return RunPhaseBreakdown(
        runs=len(per_run),
        runs_without_start=runs_without_start,
        queue_wait=_phase_stats(queue_wait),
        first_text=_phase_stats(first_text),
        model_turn=_phase_stats(model_turn),
        tool_call=_phase_stats(tool_call),
        settle=_phase_stats(settle),
    )


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/agent/latency", response_model=AgentLatencyOut)
def agent_latency(
    request: Request,
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AgentLatencyOut:
    """Agent 链路延迟观测：steward 辅助分 kind 分位数 + assistant run 总时长。

    只读聚合元数据；不返回 prompt、候选、checkpoint 等业务内容（与 admin
    steward/agent 域同一字段白名单纪律）。
    """
    admin, _account = identity
    cutoff = utcnow() - timedelta(days=days)
    steward_assist = _assist_stats(db, cutoff)
    run_totals, runs_by_status = _run_stats(db, cutoff)
    result = AgentLatencyOut(
        generated_at=utcnow(),
        window_days=days,
        steward_assist=steward_assist,
        assistant_runs=run_totals,
        assistant_phases=_phase_breakdown(db, cutoff),
        runs_by_status=runs_by_status,
        notes=list(_OBSERVATION_NOTES),
    )
    admin_audit.record_access(
        db,
        action="agent_latency.read",
        endpoint=_ENDPOINT,
        system_admin_id=admin.id,
        filters={"days": days},
        result_count=len(steward_assist),
        ip=_ip(request),
    )
    db.commit()
    return result
