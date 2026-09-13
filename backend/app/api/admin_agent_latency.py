"""Agent 链路延迟观测（09-13-agent-latency-tuning；仅 admin_app :8002，只读）。

口径合同（由现有 schema 决定，避免伪精度）：
- ``steward_model_calls.latency_ms`` 是每次模型调用真实耗时；``error_code=timeout``
  的调用在 ``STEWARD_ASSIST_TIMEOUT_SECONDS`` 处被截断（删失样本），分位数对
  含删失样本的全体计算，超时单列计数——高超时占比时 p50/p95 会偏小，解读需结合
  timeout 计数；
- ``agent_runs`` 只有 ``created_at``/``settled_at`` 与心跳续期的
  ``lease_expires_at``：「入队→终态」总时长可精确计算；「被租走」「首事件」
  没有直接时间戳（lease_expires_at 被心跳持续前移、首个事件可能入队即产生），
  因此不输出分段列；若后续需要精确分段，应在 FSM 转换点补生命周期事件
  （见任务 PRD 记录，避免本任务为观测改运行时热路径）。
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminPrincipal, require_admin_ready
from app.api.deps import get_db
from app.models.agent import AgentRun
from app.models.steward import StewardModelCall
from app.services import admin_audit
from app.utils.timeutil import utcnow

router = APIRouter(prefix="/admin-api/v1", tags=["admin-agent-latency"])
_ENDPOINT = "/admin-api/v1/agent/latency"

_OBSERVATION_NOTES: tuple[str, ...] = (
    "steward assist 分位数包含 error_code=timeout 的删失样本（在超时预算处截断），"
    "高 timeout 占比时 p50/p95 偏小；timeout 计数单列给出",
    "assistant run 仅提供入队→终态总时长；被租走/首事件时刻未落库"
    "（lease_expires_at 被心跳续期），分段观测需后续在 FSM 转换点补生命周期事件",
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


class AgentLatencyOut(BaseModel):
    generated_at: datetime
    window_days: int
    steward_assist: dict[str, AssistKindLatency]
    assistant_runs: RunTotalLatency
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
