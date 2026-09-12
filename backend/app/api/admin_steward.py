"""Admin Steward 运维端点（09-11 R5；仅 admin_app :8002，前缀 /admin-api/v1）。

信任边界（design 合同）：
- 读状态/读作业不受引擎启用门禁：即使 STEWARD_ENABLED/STEWARD_WORKER_ENABLED
  关闭，管理员也必须能读到 disabled/paused 的可解释状态（不挂在
  AGENT_RUNTIME_ENABLED 门禁下，与 admin_agent router 刻意分开）；
- 人工重跑受 STEWARD_ENABLED 门禁（503）；要求 reason + expected_policy_version
  + Idempotency-Key；单空间冷却 429、策略冲突 409；未知/不可见空间同形 404
  （不泄露存在性）；
- 字段白名单：作业只暴露 job_id/space_id/cause/status/attempt/available_at/
  error_code；系统管理员不获得人物、候选、图谱、prompt、checkpoint 原文访问权；
- 审计（admin_access_audits）只保存安全理由分类 + 关联 ID；reason 原文、
  checkpoint、error_json 永不入审计或日志。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.admin_deps import AdminPrincipal, require_admin_ready
from app.api.deps import get_db
from app.errors import (
    SPACE_NOT_FOUND,
    STEWARD_DISABLED,
    STEWARD_POLICY_CONFLICT,
    STEWARD_RERUN_TOO_FREQUENT,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.personal_family_view import PersonalFamilyView
from app.models.space import FamilySpace
from app.models.steward import (
    STEWARD_JOB_STATUSES,
    ActionCard,
    StewardJob,
    StewardModelCall,
    StewardSpaceSchedule,
)
from app.schemas.admin_steward import (
    StewardAlertOut,
    StewardJobOut,
    StewardJobsPageOut,
    StewardMetricsOut,
    StewardRerunAccepted,
    StewardRerunRequest,
    StewardStatusOut,
    StewardSwitchStateOut,
)
from app.services import admin_audit, steward
from app.utils import timeutil

router = APIRouter(prefix="/admin-api/v1", tags=["admin-steward"])

_ENDPOINT = "/admin-api/v1/steward"
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100

# 安全理由分类（audit 只存分类码，不存 reason 原文）
_REASON_CLASSES = {
    "conflict": ("conflict", "冲突", "矛盾"),
    "missed_events": ("missed", "遗漏", "停机", "补"),
    "card_issue": ("card", "卡片", "到期"),
}


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _classify_reason(reason: str) -> str:
    """把自由文本 reason 归类为安全分类码；映射不到的归 general。"""
    lowered = reason.lower()
    for klass, markers in _REASON_CLASSES.items():
        if any(marker in lowered for marker in markers):
            return klass
    return "general"


def _audit(
    session: Session,
    identity: AdminPrincipal,
    request: Request,
    *,
    action: str,
    target_id: int | None = None,
    filters: dict[str, Any] | None = None,
    result_count: int | None = None,
) -> None:
    admin, _account = identity
    admin_audit.record_access(
        session,
        action=action,
        endpoint=_ENDPOINT,
        system_admin_id=admin.id,
        target_type="space" if target_id is not None else None,  # CHECK 只允许 user/space
        target_id=target_id,
        filters=filters,
        result_count=result_count,
        ip=_client_ip(request),
    )


def _switch(key: str, enabled: bool, on_text: str, off_text: str) -> StewardSwitchStateOut:
    return StewardSwitchStateOut(
        key=key, enabled=enabled, explanation=on_text if enabled else off_text
    )


def _require_rerun_enabled() -> None:
    if not config.STEWARD_ENABLED:
        raise_api_error(503, STEWARD_DISABLED, "Steward 功能未开启，无法重跑")


@router.get("/steward/status", response_model=StewardStatusOut)
def steward_status(
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> StewardStatusOut:
    """有效开关、worker 心跳、队列计数与最近安全错误码（AC-1：全组合可解释）。

    只开模型设置（STEWARD_ASSIST_* / 空间辅助开关）不改变 state：核心引擎
    STEWARD_ENABLED 关闭时 state 恒为 disabled。
    """
    core = config.STEWARD_ENABLED
    worker = config.STEWARD_WORKER_ENABLED
    assist_any = (
        config.STEWARD_ASSIST_CANDIDATE
        or config.STEWARD_ASSIST_RANKING
        or config.STEWARD_ASSIST_EXPLANATION
    )
    switches = [
        _switch(
            "core",
            core,
            "Steward 核心引擎已启用（事件/扫描可登记作业）",
            "Steward 核心引擎关闭：不登记也不执行任何作业（模型设置不影响本状态）",
        ),
        _switch(
            "worker",
            worker,
            "进程内 worker 已启用：queued 作业被周期泵执行",
            "worker 关闭：作业可排队但不会被执行（paused 语义）",
        ),
        _switch(
            "relation_engine",
            core,
            "关系重算引擎随核心引擎运行（DerivedFact/PFV 重建）",
            "关系重算引擎未运行（随核心引擎关闭）",
        ),
        _switch(
            "pfv",
            config.PERSONAL_FAMILY_VIEW_ENABLED,
            "PersonalFamilyView 投影已启用",
            "PersonalFamilyView 投影关闭",
        ),
        _switch(
            "model_assist_platform",
            assist_any,
            "平台级模型辅助至少一类开启（仍需空间级开关同时打开才实际调用）",
            "平台级模型辅助全关：Steward 行为与确定性基线等价",
        ),
        _switch(
            "model_assist_spaces",
            assist_any and core,
            "空间级辅助开关按空间配置生效（有效 = 平台 AND 空间）",
            "无任何空间辅助在生效（平台或空间开关未同时打开）",
        ),
    ]
    if not core:
        state = "disabled"
    elif not worker:
        state = "paused"
    else:
        state = "running"

    queue_counts: dict[str, int] = {"queued": 0, "leased": 0, "running": 0}
    oldest_queued_seconds: int | None = None
    recent_error_codes: list[str] = []
    worker_heartbeat_at: datetime | None = None
    alerts: list[StewardAlertOut] = []
    if core:
        for status in queue_counts:
            queue_counts[status] = int(
                db.scalar(select(sa.func.count()).where(StewardJob.status == status)) or 0
            )
        oldest = db.scalar(
            select(StewardJob.created_at)
            .where(StewardJob.status == "queued")
            .order_by(StewardJob.created_at.asc())
            .limit(1)
        )
        if oldest is not None:
            oldest_queued_seconds = max(0, int((timeutil.utcnow() - oldest).total_seconds()))
        recent_error_codes = [
            code
            for code in db.scalars(
                select(StewardJob.error_code)
                .where(StewardJob.status == "failed", StewardJob.error_code.is_not(None))
                .order_by(StewardJob.settled_at.desc())
                .limit(10)
            ).all()
            if code is not None
        ]
        worker_heartbeat_at = db.scalar(select(sa.func.max(StewardJob.heartbeat_at)))

        # ---- 可见告警（R2/AC-4）：阈值可配置（0 = 自动 = max(2×扫描间隔, 60)）----
        # queue_backlog：核心已启用且最老 queued 年龄超阈值（paused 也算 —— 积压
        # 是真实状态，但 state 只在 worker 开启且无进展时才升级 degraded）。
        # queue_stalled：worker 已启用但连续两个扫描窗口（≥2×间隔）没有任何作业
        # 结算进展 —— worker 进程已停而 HTTP 存活的故障形态，必须 degraded。
        threshold = (
            config.STEWARD_ALERT_QUEUE_SECONDS
            if config.STEWARD_ALERT_QUEUE_SECONDS > 0
            else max(2 * config.STEWARD_SCAN_INTERVAL_SECONDS, 60)
        )
        if oldest_queued_seconds is not None and oldest_queued_seconds > threshold:
            alerts.append(
                StewardAlertOut(
                    code="queue_backlog",
                    detail={
                        "oldest_queued_seconds": oldest_queued_seconds,
                        "threshold_seconds": threshold,
                    },
                )
            )
        last_settled = db.scalar(select(sa.func.max(StewardJob.settled_at)))
        window = 2 * config.STEWARD_SCAN_INTERVAL_SECONDS
        stalled = (
            queue_counts["queued"] > 0
            and worker
            and oldest_queued_seconds is not None
            and oldest_queued_seconds > window
            and (
                last_settled is None or (timeutil.utcnow() - last_settled).total_seconds() > window
            )
        )
        if stalled:
            alerts.append(
                StewardAlertOut(
                    code="queue_stalled",
                    detail={
                        "last_settled_at": last_settled.isoformat() if last_settled else None,
                        "window_seconds": window,
                    },
                )
            )
        if state == "running" and (recent_error_codes or stalled):
            state = "degraded"
    metrics = _collect_metrics(
        db,
        core=core,
        core_queue_depth=queue_counts["queued"],
        oldest_queued_age_seconds=oldest_queued_seconds,
    )
    _audit(db, identity, request, action="steward.status", result_count=None)
    db.commit()
    return StewardStatusOut(
        state=state,  # type: ignore[arg-type]
        switches=switches,
        worker_heartbeat_at=worker_heartbeat_at,
        queue_counts=queue_counts,
        oldest_queued_seconds=oldest_queued_seconds,
        recent_error_codes=list(dict.fromkeys(recent_error_codes))[:5],
        metrics=metrics,
        alerts=alerts,
    )


def _collect_metrics(
    db: Session,
    *,
    core: bool,
    core_queue_depth: int,
    oldest_queued_age_seconds: int | None,
) -> StewardMetricsOut:
    """从真实 DB 状态聚合观测指标（R2）。

    聚合只产生全局计数/时间戳，不含任何家庭内容；本端点仅系统管理员可达
    （8002 admin listener），家庭 API 不暴露任何同类聚合 —— 普通成员永远
    拿不到跨空间计数。
    """
    last_scan_at: datetime | None = None
    last_worker_tick_at: datetime | None = None
    core_failed = 0
    if core:
        last_scan_at = db.scalar(select(sa.func.max(StewardSpaceSchedule.updated_at)))
        last_settled = db.scalar(select(sa.func.max(StewardJob.settled_at)))
        last_heartbeat = db.scalar(select(sa.func.max(StewardJob.heartbeat_at)))
        candidates = [t for t in (last_settled, last_heartbeat) if t is not None]
        last_worker_tick_at = max(candidates) if candidates else None
        core_failed = int(
            db.scalar(select(sa.func.count()).where(StewardJob.status == "failed")) or 0
        )
    # 辅助层：attempt 行状态计数 + 预算（reserved/in_flight 预留，终态按
    # billed_tokens 保守计费）。辅助关闭时这些计数自然为 0（config-off 可区分）。
    assist_counts = {
        status: int(
            db.scalar(select(sa.func.count()).where(StewardModelCall.status == status)) or 0
        )
        for status in ("failed", "degraded", "unknown")
    }
    budget_reserved = int(
        db.scalar(
            select(sa.func.coalesce(sa.func.sum(StewardModelCall.reserved_input_tokens), 0)).where(
                StewardModelCall.status.in_(("reserved", "in_flight"))
            )
        )
        or 0
    ) + int(
        db.scalar(
            select(sa.func.coalesce(sa.func.sum(StewardModelCall.reserved_output_tokens), 0)).where(
                StewardModelCall.status.in_(("reserved", "in_flight"))
            )
        )
        or 0
    )
    budget_consumed = int(
        db.scalar(
            select(sa.func.coalesce(sa.func.sum(StewardModelCall.billed_tokens), 0)).where(
                StewardModelCall.billed_tokens.is_not(None)
            )
        )
        or 0
    )
    pfv_stale = int(
        db.scalar(select(sa.func.count()).where(PersonalFamilyView.status == "stale")) or 0
    )
    cards_created = int(db.scalar(select(sa.func.count()).select_from(ActionCard)) or 0)
    cards_superseded = int(
        db.scalar(
            select(sa.func.count()).select_from(ActionCard).where(ActionCard.state == "superseded")
        )
        or 0
    )
    return StewardMetricsOut(
        core_queue_depth=core_queue_depth,
        oldest_queued_age_seconds=oldest_queued_age_seconds,
        last_scan_at=last_scan_at,
        last_worker_tick_at=last_worker_tick_at,
        core_failed=core_failed,
        assist_failed=assist_counts["failed"],
        assist_degraded=assist_counts["degraded"],
        assist_unknown=assist_counts["unknown"],
        budget_reserved_tokens=budget_reserved,
        budget_consumed_tokens=budget_consumed,
        pfv_stale=pfv_stale,
        cards_created=cards_created,
        cards_superseded=cards_superseded,
    )


@router.get("/steward/jobs", response_model=StewardJobsPageOut)
def steward_jobs(
    request: Request,
    space_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE_DEFAULT, ge=1, le=_PAGE_SIZE_MAX),
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> StewardJobsPageOut:
    """作业元数据列表（字段白名单；page_size 默认 20、最大 100）。"""
    if status is not None and status not in STEWARD_JOB_STATUSES:
        raise_api_error(422, VALIDATION_ERROR, "未知作业状态", detail={"status": status})
    stmt = select(StewardJob).order_by(StewardJob.id.desc())
    if space_id is not None:
        stmt = stmt.where(StewardJob.space_id == space_id)
    if status is not None:
        stmt = stmt.where(StewardJob.status == status)
    rows = db.scalars(stmt.limit(page_size).offset((page - 1) * page_size)).all()
    items = [
        StewardJobOut(
            job_id=row.id,
            space_id=row.space_id,
            cause=row.cause,
            status=row.status,
            attempt=row.attempt,
            available_at=row.available_at,
            error_code=row.error_code,
        )
        for row in rows
    ]
    _audit(
        db,
        identity,
        request,
        action="steward.jobs",
        target_id=space_id,
        filters={"status": status, "page": page, "page_size": page_size},
        result_count=len(items),
    )
    db.commit()
    return StewardJobsPageOut(items=items, page=page, page_size=page_size)


def _find_idempotent_rerun(db: Session, space_id: int, idempotency_key: str) -> StewardJob | None:
    """同 (space, Idempotency-Key) 的历史重跑作业：键存 checkpoint_json。"""
    key_expr = sa.func.json_extract(StewardJob.checkpoint_json, "$.idempotency_key")
    return db.scalar(
        select(StewardJob)
        .where(StewardJob.space_id == space_id, key_expr == idempotency_key)
        .order_by(StewardJob.id.desc())
        .limit(1)
    )


@router.post(
    "/steward/spaces/{space_id}/rerun", response_model=StewardRerunAccepted, status_code=202
)
def steward_rerun(
    space_id: int,
    body: StewardRerunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> StewardRerunAccepted:
    """单空间人工重跑：幂等键去重、策略冲突 409、冷却 429、关闭 503。

    终态历史作业不复活：重跑创建关联新作业（retry_of_job_id）；已有活跃作业
    时合并（coalesced=true）。审计只存理由分类与关联 ID。
    """
    _require_rerun_enabled()
    if not idempotency_key or not idempotency_key.strip():
        raise_api_error(422, VALIDATION_ERROR, "缺少 Idempotency-Key 请求头")
    key = idempotency_key.strip()
    space = db.get(FamilySpace, space_id)
    if space is None:
        # 未知空间与不可见空间同形安全拒绝（后台不持有家庭空间可见性语义）
        raise_api_error(404, SPACE_NOT_FOUND, "空间不存在或不可见")
    admin, _account = identity

    def _done(job: StewardJob, coalesced: bool) -> StewardRerunAccepted:
        _audit(
            db,
            identity,
            request,
            action="steward.rerun",
            target_id=space_id,
            filters={
                "reason_class": _classify_reason(body.reason),
                "idempotency_key": key,
                "job_id": job.id,
                "coalesced": coalesced,
            },
            result_count=1,
        )
        db.commit()
        return StewardRerunAccepted(job_id=job.id, coalesced=coalesced)

    replay = _find_idempotent_rerun(db, space_id, key)
    if replay is not None:
        return _done(replay, coalesced=True)
    if body.expected_policy_version != steward.POLICY_VERSION:
        db.commit()
        raise_api_error(
            409,
            STEWARD_POLICY_CONFLICT,
            "expected_policy_version 与现行策略版本不一致",
            detail={"expected": body.expected_policy_version},
        )
    last_rerun = db.scalar(
        select(StewardJob)
        .where(StewardJob.space_id == space_id, StewardJob.cause == "admin_rerun")
        .order_by(StewardJob.id.desc())
        .limit(1)
    )
    if (
        last_rerun is not None
        and config.STEWARD_RERUN_COOLDOWN_SECONDS > 0
        and last_rerun.created_at
        and (timeutil.utcnow() - last_rerun.created_at).total_seconds()
        < config.STEWARD_RERUN_COOLDOWN_SECONDS
    ):
        db.commit()
        raise_api_error(
            429,
            STEWARD_RERUN_TOO_FREQUENT,
            "该空间重跑过于频繁，请稍后再试",
        )
    active = db.scalar(
        select(StewardJob).where(
            StewardJob.space_id == space_id,
            StewardJob.status.in_(("queued", "leased", "running")),
        )
    )
    if active is not None:
        return _done(active, coalesced=True)
    previous = db.scalar(
        select(StewardJob)
        .where(StewardJob.space_id == space_id)
        .order_by(StewardJob.id.desc())
        .limit(1)
    )
    job, _created = steward.enqueue_steward_job(
        db,
        space_id=space_id,
        cause="admin_rerun",
        trigger_cursor=steward.current_event_watermark(db),
        retry_of_job_id=previous.id if previous is not None else None,
    )
    checkpoint = dict(job.checkpoint_json or {})
    checkpoint["idempotency_key"] = key
    checkpoint["reason_class"] = _classify_reason(body.reason)
    job.checkpoint_json = checkpoint
    db.flush()
    return _done(job, coalesced=False)
