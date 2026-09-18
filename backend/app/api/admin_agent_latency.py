"""Agent 链路延迟观测（09-13-agent-latency-tuning；仅 admin_app :8002，只读）。

口径合同（由现有 schema 决定，避免伪精度）：
- ``steward_model_calls.latency_ms`` 是每次模型调用真实耗时；``error_code=timeout``
  的调用在 ``STEWARD_ASSIST_TIMEOUT_SECONDS`` 处被截断（删失样本），分位数对
  含删失样本的全体计算，超时单列计数——高超时占比时 p50/p95 会偏小，解读需结合
  timeout 计数；
- ``agent_runs.lease_expires_at`` 被心跳持续前移，**不能**用它反推被租走时刻；
  首次取得执行权的权威时刻是 ``agent_runs.first_leased_at``（attempt 0→1 写一次，不可变）；
- ``agent_run_events.created_at`` 是**后端入库时刻**，而 sidecar 默认每 250ms 批量
  flush：同一批内先后产生的阶段会退化为相差约 1ms（实测合成 125ms 工具执行与其
  结束事件相差 1.301ms）。因此持久事件间隔**不能**当作精确执行耗时；
- 精确阶段时长来自 sidecar 的源计时（``agent_run_events.timing_json``，见
  ``schemas/agent.EventTimingIn``）：它表达 producer 单调测量，旧行为 NULL 按
  unknown 处理。``run.started`` 由 SDK ``agent_start`` 产生，晚于 context 获取与
  session 创建，所以「取得执行权→SDK 开始」的准备时长单列为 ``prepare``，不混入
  排队等待。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminPrincipal, require_admin_ready
from app.api.deps import get_db
from app.models.agent import AgentRun, AgentRunEvent
from app.models.audit_log import AuditLog
from app.models.steward import StewardModelCall
from app.services import admin_audit
from app.utils.timeutil import utcnow

router = APIRouter(prefix="/admin-api/v1", tags=["admin-agent-latency"])
_ENDPOINT = "/admin-api/v1/agent/latency"

_OBSERVATION_NOTES: tuple[str, ...] = (
    "steward assist 分位数包含 error_code=timeout 的删失样本（在超时预算处截断），"
    "高 timeout 占比时 p50/p95 偏小；timeout 计数单列给出",
    "assistant 阶段时长有两类来源：source_clock=后端状态转换或 sidecar 源计时"
    "（timing_json，producer 单调测量，精确）；persisted_interval=持久事件 created_at 之差。"
    "后者受 sidecar 250ms 批量 flush 影响，同批内的阶段会退化为相差约 1ms（实测合成"
    "125ms 工具执行与其结束事件相差 1.301ms），**不能**当作精确执行耗时；每个阶段用"
    "basis/native_n/derived_n 标注样本构成，只有源计时存在的阶段才报告精确值",
    "queue_wait 用 agent_runs.first_leased_at − created_at（首次取得执行权，attempt 0→1 写入"
    "一次，不可变）；lease_expires_at 被心跳续期，不参与任何分段计算。run.started 由 SDK"
    "agent_start 产生，晚于 context/session 准备，故 prepare（run.started 的 timing_json）单列，"
    "不得归入排队",
    "first_text 每 run 一个样本（首次取得执行权→首个 assistant 正文）；model_turn 每轮一个"
    "样本（该轮 turn.started→该轮 assistant 正文），多轮 run 会贡献多个样本，两者不可混算",
    "tool_call 按 tool_call_id 配对；未配对（运行中/中断）的调用不计入样本",
    "compaction 是 model_turn 的子成分（该轮内 SDK compaction_start→compaction_end 累计时长，"
    "含摘要请求），单独统计才能区分摘要与生成；仅当 sidecar 上报源计时时才有样本，"
    "无压缩即 n=0，不用 0 填充。读 model_turn 时必须同时参考 compaction 与 provider_retry，"
    "否则不得声称该值是纯推理时间",
    "无事件的 run 与未取得执行权的 run 分别计数（runs_without_events / "
    "runs_without_first_lease / runs_without_start），不从事件集合反推分母",
    "provider_retry 是上游 5xx/408/409/429 重试开销的**下界**，由 agent_provider_egress 审计"
    "（target_id 即 run_id）推导：同一连续失败段内 末次失败 − 首次失败（含段内退避与重试耗时）。"
    "审计只记完成时刻、不记请求开始，所以**首次失败尝试自身的耗时不可知**；"
    "段长为 1（一次失败后即成功）的重试真实存在但窗口为 0，单列为 unmeasured_retries，"
    "不得因此反推「无重试」。该开销同时包含在 model_turn 内（重试发生在同一轮"
    "turn.started→正文之间），读 model_turn 时需参考这些计数才能区分重试与纯生成。"
    "自 E 起审计带 retryable 安全分类：retryable=false 的失败（上游永久拒绝、取消/失租、"
    "流中断）不计入重试次数，避免虚假重试段；历史行无该字段时沿用旧的 failed 口径",
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
    """单阶段耗时分布（毫秒）；空样本 n=0 且分位为 null，不零填充。

    ``basis`` 声明该分布的样本来源，``native_n``/``derived_n`` 给出构成：

    - ``source_clock``：后端状态转换或 sidecar 源计时（精确）；
    - ``persisted_interval``：持久事件 ``created_at`` 之差（受 flush 批量量化）；
    - ``mixed``：优先源计时、缺失时回退持久间隔（历史行）。
    """

    n: int
    p50_ms: int | None = None
    p95_ms: int | None = None
    max_ms: int | None = None
    basis: Literal["source_clock", "persisted_interval", "mixed", "none"] = "none"
    native_n: int = 0
    derived_n: int = 0


class ProviderRetryStats(BaseModel):
    """上游重试：**次数**无歧义，**时长**是下界（见 notes）。"""

    failed_attempts: int
    runs_with_failure: int
    runs_with_retry: int
    retry_segments: int
    unmeasured_retries: int
    exhausted_segments: int
    duration_lower_bound: PhaseStats


class RunPhaseBreakdown(BaseModel):
    """assistant run 分段：源计时优先，历史行回退持久事件间隔。"""

    runs: int
    runs_without_events: int
    runs_without_first_lease: int
    runs_without_start: int
    queue_wait: PhaseStats
    prepare: PhaseStats
    first_text: PhaseStats
    model_turn: PhaseStats
    tool_call: PhaseStats
    compaction: PhaseStats
    settle: PhaseStats
    provider_retry: ProviderRetryStats


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


def _phase_stats(values: list[int], *, native_n: int = 0) -> PhaseStats:
    """单阶段分布；``values`` 是该阶段所有可测样本，``native_n`` 是其中源计时的数量。"""
    ordered = sorted(values)
    derived_n = len(ordered) - native_n
    if not ordered:
        basis: Literal["source_clock", "persisted_interval", "mixed", "none"] = "none"
    elif derived_n == 0:
        basis = "source_clock"
    elif native_n == 0:
        basis = "persisted_interval"
    else:
        basis = "mixed"
    return PhaseStats(
        n=len(ordered),
        p50_ms=_nearest_rank(ordered, 0.50),
        p95_ms=_nearest_rank(ordered, 0.95),
        max_ms=ordered[-1] if ordered else None,
        basis=basis,
        native_n=native_n,
        derived_n=derived_n,
    )


def _timing_ms(payload: dict[str, Any] | None) -> int | None:
    """从持久 timing_json 取出 producer 测量时长；缺失/非法即 None（unknown）。"""
    if not isinstance(payload, dict):
        return None
    value = payload.get("duration_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _timing_compaction_ms(payload: dict[str, Any] | None) -> int | None:
    """该轮内 SDK 压缩时长（``duration_ms`` 的子成分）；无压缩/非法即 None。

    不用 0 填充：0 会与“确实测到 0ms 压缩”混淆，缺省才表示本轮没有压缩。
    """
    if not isinstance(payload, dict):
        return None
    value = payload.get("compaction_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _provider_retry_windows(
    db: Session, run_ids: list[int]
) -> tuple[dict[int, list[int]], ProviderRetryStats]:
    """按 run 推导上游重试开销下界（毫秒）与失败尝试数。

    ``agent_provider_egress`` 的 ``target_id`` 就是 run_id，``detail_json.status``
    为 succeeded/failed。重试由 pi-ai 在 5xx/408/409/429 上指数退避完成，发生在同一轮
    ``turn.started``→正文之间，因此该开销**已被计入 model_turn**；本函数把它单独拆出，
    供读者从 model_turn 中区分重试与纯生成（A-02：重试不得当作单次推理）。

    时长语义是**下界**：审计只记请求完成时刻、不记开始时刻，故段内**首次**失败
    自身耗时不可知。段长（连续失败次数）为 1 的段（一次失败后即成功）真实存在但
    窗口为 0，单列 ``unmeasured_retries``；**不得**用它的 n 反推「无重试」。
    因此失败尝试数/重试段数（无歧义）与时长（下界）必须成对解读。

    不读/不记 prompt、响应正文。
    """
    empty = ProviderRetryStats(
        failed_attempts=0,
        runs_with_failure=0,
        runs_with_retry=0,
        retry_segments=0,
        unmeasured_retries=0,
        exhausted_segments=0,
        duration_lower_bound=_phase_stats([]),
    )
    if not run_ids:
        return {}, empty
    rows = db.execute(
        select(AuditLog.target_id, AuditLog.detail_json, AuditLog.created_at)
        .where(
            AuditLog.action == "agent_provider_egress",
            AuditLog.target_id.in_(run_ids),
        )
        .order_by(AuditLog.target_id, AuditLog.created_at)
    ).all()

    windows: dict[int, list[int]] = {}
    failed_attempts = 0
    runs_with_failure: set[int] = set()
    retry_segments = 0
    unmeasured_retries = 0
    exhausted_segments = 0
    # 当前失败段的首次/最近一次失败时刻与段内失败次数，按 run 独立跟踪。
    streak_first: dict[int, datetime] = {}
    streak_last: dict[int, datetime] = {}
    streak_count: dict[int, int] = {}

    def close_streak(run_id: int) -> None:
        """段以成功/窗口结束收尾；每次收尾恰好统计一次。"""
        nonlocal retry_segments, unmeasured_retries
        first = streak_first.pop(run_id, None)
        last = streak_last.pop(run_id, None)
        count = streak_count.pop(run_id, 0)
        if first is None or last is None:
            return
        if count <= 1:
            # 单次失败后即成功：重试真实发生，但窗口长度为 0，不可测。
            unmeasured_retries += 1
            return
        retry_segments += 1
        if last > first:
            windows.setdefault(run_id, []).append(_ms(last - first))

    for run_id, detail_json, at in rows:
        if run_id is None:
            continue
        try:
            detail = json.loads(detail_json or "{}")
        except (TypeError, ValueError):
            continue
        status = detail.get("status")
        if status == "failed":
            # E-AC5：本任务给 failed 补上了安全分类（error_class/retryable）。
            # 只有**会被重试**的失败才是 provider_retry 的组成（上游暂时错误、
            # 传输层失败）；永久拒绝（upstream_rejected）、取消/失租
            # （run_cancelled）与流中断（stream_interrupted）不是重试，计入会
            # 造出虚假重试段与虚假失败数。历史行无 retryable 字段时保持原语义
            # （按 failed 计），不回填也不改写旧数据。
            if detail.get("retryable") is False:
                close_streak(run_id)
                continue
            failed_attempts += 1
            runs_with_failure.add(run_id)
            streak_first.setdefault(run_id, at)
            streak_last[run_id] = at
            streak_count[run_id] = streak_count.get(run_id, 0) + 1
            continue
        # 非失败（succeeded/blocked_by_policy）：结束本段。
        close_streak(run_id)

    # 审计以失败结尾（失败耗尽或仍活跃）：同样收尾，不丢弃尾部失败段。
    for run_id in list(streak_first):
        if streak_count.get(run_id, 0) > 1:
            exhausted_segments += 1
        close_streak(run_id)

    stats = ProviderRetryStats(
        failed_attempts=failed_attempts,
        runs_with_failure=len(runs_with_failure),
        runs_with_retry=len(windows),
        retry_segments=retry_segments,
        unmeasured_retries=unmeasured_retries,
        exhausted_segments=exhausted_segments,
        duration_lower_bound=_phase_stats([v for values in windows.values() for v in values]),
    )
    return windows, stats


def _phase_breakdown(db: Session, cutoff: datetime) -> RunPhaseBreakdown:
    """按 run 分解 assistant 耗时；分母来自 run 表，不从事件集合反推。

    每个阶段优先用 producer 源计时（精确）；历史行无源计时时回退到持久事件
    ``created_at`` 间隔，并用 ``basis``/``native_n``/``derived_n`` 如实标注，
    不把批量 flush 量化后的间隔当作精确执行耗时。
    """
    runs = db.execute(
        select(
            AgentRun.id,
            AgentRun.created_at,
            AgentRun.settled_at,
            AgentRun.first_leased_at,
        ).where(AgentRun.created_at >= cutoff)
    ).all()
    created_at = {run_id: created for run_id, created, _, _ in runs}
    settled_at = {run_id: settled for run_id, _, settled, _ in runs}
    first_leased_at = {run_id: leased for run_id, _, _, leased in runs}

    rows = db.execute(
        select(
            AgentRunEvent.run_id,
            AgentRunEvent.type,
            AgentRunEvent.created_at,
            AgentRunEvent.public_payload,
            AgentRunEvent.timing_json,
        )
        .join(AgentRun, AgentRun.id == AgentRunEvent.run_id)
        .where(AgentRun.created_at >= cutoff)
        .order_by(AgentRunEvent.run_id, AgentRunEvent.seq)
    ).all()

    per_run: dict[int, list[tuple[str, datetime, dict[str, Any], dict[str, Any] | None]]] = {}
    for run_id, event_type, at, payload, timing in rows:
        per_run.setdefault(run_id, []).append((event_type, at, payload or {}, timing))

    retry_windows, provider_retry = _provider_retry_windows(db, sorted(created_at))

    # (值, 是否源计时)；同一列表内保留来源标记以便统计 native_n。
    queue_wait: list[tuple[int, bool]] = []
    prepare: list[tuple[int, bool]] = []
    first_text: list[tuple[int, bool]] = []
    model_turn: list[tuple[int, bool]] = []
    tool_call: list[tuple[int, bool]] = []
    compaction: list[tuple[int, bool]] = []
    settle: list[tuple[int, bool]] = []
    runs_without_first_lease = 0
    runs_without_start = 0

    for run_id in sorted(created_at):
        events = per_run.get(run_id, [])
        created = created_at[run_id]
        # 排队等待：入队 → 首次取得执行权（不可变），与续租字段无关。
        leased = first_leased_at.get(run_id)
        if leased is None:
            runs_without_first_lease += 1
        else:
            queue_wait.append((_ms(leased - created), True))

        started_event = next(
            ((at, timing) for kind, at, _, timing in events if kind == "run.started"), None
        )
        if started_event is None:
            # 取得执行权前就结束（queued 取消等）：无 run.started，不伪造分段。
            runs_without_start += 1
        elif leased is not None:
            # 准备阶段：取得执行权 → SDK agent_start（context + session 创建）。
            # 优先 producer 源计时；否则用两次后端转换的间隔（同一时钟，精确）。
            native = _timing_ms(started_event[1])
            prepare.append(
                (
                    native if native is not None else _ms(started_event[0] - leased),
                    native is not None,
                )
            )

        if started_event is not None:
            first = next(
                (
                    (at, timing)
                    for kind, at, _, timing in events
                    if kind == "message.assistant_added"
                ),
                None,
            )
            if first is not None:
                native = _timing_ms(first[1])
                first_text.append(
                    (
                        native if native is not None else _ms(first[0] - started_event[0]),
                        native is not None,
                    )
                )

        # 每轮模型生成 = 该轮 turn.started → 该轮内首个 assistant 正文。
        # 只在本轮内查找（下一个 turn.started 之前），因此无正文的工具轮
        # 不会把下一轮的正文误算进自己；无正文即无样本。
        # 多轮 run 贡献多个样本，与 per-run 的 first_text 分开统计。
        for index, (kind, at, _payload, _timing) in enumerate(events):
            if kind != "turn.started":
                continue
            produced: tuple[datetime, dict[str, Any] | None] | None = None
            for later_kind, later, _later_payload, later_timing in events[index + 1 :]:
                if later_kind == "turn.started":
                    break
                if later_kind == "message.assistant_added":
                    produced = (later, later_timing)
                    break
            if produced is None:
                continue
            native = _timing_ms(produced[1])
            model_turn.append(
                (native if native is not None else _ms(produced[0] - at), native is not None)
            )
            # 该轮的压缩是 model_turn 的子成分，单独收集以区分摘要请求与生成；
            # 它同样只在有源计时时才有意义（历史行无法区分）。
            measured = _timing_compaction_ms(produced[1])
            if measured is not None:
                compaction.append((measured, True))

        open_tools: dict[str, tuple[datetime, dict[str, Any] | None]] = {}
        for kind, at, payload, timing in events:
            if kind not in ("tool.execution.started", "tool.execution.completed"):
                continue
            key = str(payload.get("tool_call_id", ""))
            if kind == "tool.execution.started":
                open_tools[key] = (at, timing)
            else:
                opened = open_tools.pop(key, None)
                if opened is not None:
                    native = _timing_ms(timing)
                    tool_call.append(
                        (
                            native if native is not None else _ms(at - opened[0]),
                            native is not None,
                        )
                    )

        # 结算开销 = 最后一个非终态事件（正文/工具落库）→ run 终态，
        # 即事件 flush 与 settle 写入的尾部成本；不是拿终态事件和自身相减。
        # 两个时刻都是后端 UTC，故始终是 source_clock。
        settled = settled_at.get(run_id)
        if settled is not None:
            terminal = {"run.settled", "run.failed", "run.cancelled", "run.expired"}
            tail = [at for kind, at, _, _ in events if kind not in terminal]
            if tail:
                settle.append((_ms(settled - max(tail)), True))

    def _stats(values: list[tuple[int, bool]]) -> PhaseStats:
        return _phase_stats(
            [value for value, _native in values],
            native_n=sum(1 for _value, native in values if native),
        )

    return RunPhaseBreakdown(
        runs=len(created_at),
        runs_without_events=sum(1 for run_id in created_at if not per_run.get(run_id)),
        runs_without_first_lease=runs_without_first_lease,
        runs_without_start=runs_without_start,
        queue_wait=_stats(queue_wait),
        prepare=_stats(prepare),
        first_text=_stats(first_text),
        model_turn=_stats(model_turn),
        tool_call=_stats(tool_call),
        compaction=_stats(compaction),
        settle=_stats(settle),
        provider_retry=provider_retry,
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
