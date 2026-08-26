"""Steward 空间管家：确定性后端 job 执行器（V2.4 Block S1）。

架构裁定（任务 design.md）：Steward **不调用 LLM/Provider/sidecar**；卡片文案用
模板生成。每次运行绑定 space_id + job_id + policy_version，只读取该空间确认
SourceFact、有效 DerivedFact、TermRegistry、BehaviorProjection 与 Job checkpoint；
不读取私人 Session/Memory，不访问其他空间——全部查询以 space_id 过滤，
platform_operator 角色不参与任何判定。

职责（AC-ST2/3/4）：
1. dirty 重算：消费窗口内 source_fact.* 事件 → 仅在本空间内失效并全量重建
   可见配对的 DerivedFact（全局事件也只影响本空间缓存行，绝不触碰他空间）；
2. 冲突/缺失检测：只报告（domain_events），不改图、不虚构父母；
3. 推荐资格矩阵（纯函数 services/recommendation_matrix.py）→ ActionCard
   （dedupe/evidence_version/cooldown/supersede 见 services/action_cards.py）;
4. checkpoint 幂等：作业只消费 (已完成水位, trigger_cursor] 窗口；同 cursor
   重放零副作用；崩溃整体回滚后重试不重复出卡。

红线：绝不写 SourceFact、绝不发送加入申请、绝不合并空间、绝不保存自由形式
隐藏长期记忆（checkpoint 只存进度/签名/统计）。

事务模型：整个执行包在一个立即事务内（SQLite 家族空间规模下最安全的崩溃
语义——要么全部生效要么全部回滚）；外部网络调用不存在于本模块。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    CARD_INVALID_TRANSITION,
    STEWARD_CAUSE_INVALID,
    STEWARD_DISABLED,
    STEWARD_JOB_NOT_ACTIVE,
    STEWARD_JOB_NOT_FOUND,
    STEWARD_JOB_SPACE_BUSY,
    raise_api_error,
)
from app.models.account import Account
from app.models.relationship_facts import PARENT_FACT_TYPES, SourceFact
from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.models.steward import (
    CARD_KINDS,
    STEWARD_ACTIVE_JOB_STATUSES,
    STEWARD_JOB_CAUSES,
    BehaviorProjection,
    StewardJob,
)
from app.models.user import User
from app.models.v2_foundation import DomainEvent
from app.services import action_cards, recommendation_matrix, steward_events
from app.services.action_cards import ACTION_SUPERSEDE
from app.services.derived_facts import get_or_compute
from app.services.disclosure import disclosed_categories
from app.services.domain_events import emit as emit_domain_event
from app.services.recommendation_matrix import (
    ACTION_CREATE_HOUSEHOLD,
    ACTION_REQUEST_LINEAGE,
    RecommendationInput,
    evaluate_recommendation,
)
from app.utils.timeutil import utcnow

# ---- 常量 ----
SOURCE_FACT_EVENT_PREFIX = "source_fact."
POLICY_VERSION = config.POLICY_VERSION

_ACTION_TO_KIND: dict[str, str] = {
    ACTION_CREATE_HOUSEHOLD: "household_link",
    ACTION_REQUEST_LINEAGE: "lineage_request",
}

# 行为投影键白名单前缀（红线：泛行为监控字段一律拒绝）
PROJECTION_KEY_PREFIXES = ("card_cooldown:", "correction_preference:", "term_usage:")


@contextmanager
def _immediate_tx(session: Session) -> Iterator[Session]:
    """立即事务：BEGIN IMMEDIATE 写锁前置，成功提交，异常整体回滚。"""
    sa_conn = session.connection()
    raw = sa_conn.connection.dbapi_connection
    if not isinstance(raw, sqlite3.Connection):  # pragma: no cover - 仅 SQLite 环境
        raise RuntimeError("steward queue requires a sqlite3 connection")
    if raw.in_transaction:
        raise RuntimeError("steward queue requires a clean session without pending writes")
    sa_conn.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def _require_enabled() -> None:
    if not config.STEWARD_ENABLED:
        raise_api_error(503, STEWARD_DISABLED, "Steward 功能未开启")


# ---- 行为投影（用途受限白名单）----


def put_projection(
    session: Session,
    *,
    space_id: int,
    account_id: int,
    projection_key: str,
    value: dict[str, Any],
    now: datetime | None = None,
) -> BehaviorProjection:
    """upsert 一条行为投影；projection_key 必须在白名单前缀内（fail-closed）。"""
    if not projection_key.startswith(PROJECTION_KEY_PREFIXES):
        raise_api_error(
            422, CARD_INVALID_TRANSITION, "不允许的行为投影键", detail={"key": projection_key}
        )
    row = session.scalar(
        select(BehaviorProjection).where(
            BehaviorProjection.space_id == space_id,
            BehaviorProjection.account_id == account_id,
            BehaviorProjection.projection_key == projection_key,
        )
    )
    moment = now or utcnow()
    if row is None:
        row = BehaviorProjection(
            space_id=space_id,
            account_id=account_id,
            projection_key=projection_key,
            value_json=dict(value),
            updated_at=moment,
        )
        session.add(row)
    else:
        row.value_json = dict(value)
        row.updated_at = moment
    session.flush()
    return row


def set_kind_cooldown(
    session: Session,
    *,
    space_id: int,
    account_id: int,
    kind: str,
    days: int | None = None,
    now: datetime | None = None,
) -> BehaviorProjection:
    """卡片 dismissed 后的同 kind 冷却（ST-3：相同证据不重复骚扰的时间维度）。"""
    if kind not in CARD_KINDS:
        raise_api_error(422, CARD_INVALID_TRANSITION, "未知卡片种类", detail={"kind": kind})
    moment = now or utcnow()
    until = moment + timedelta(days=days if days is not None else config.STEWARD_COOLDOWN_DAYS)
    return put_projection(
        session,
        space_id=space_id,
        account_id=account_id,
        projection_key=f"card_cooldown:{kind}",
        value={"until": until.isoformat()},
        now=moment,
    )


def kind_in_cooldown(
    session: Session, *, space_id: int, account_id: int, kind: str, now: datetime | None = None
) -> bool:
    row = session.scalar(
        select(BehaviorProjection).where(
            BehaviorProjection.space_id == space_id,
            BehaviorProjection.account_id == account_id,
            BehaviorProjection.projection_key == f"card_cooldown:{kind}",
        )
    )
    if row is None:
        return False
    raw_until = row.value_json.get("until")
    if not isinstance(raw_until, str):
        return False
    try:
        until = datetime.fromisoformat(raw_until)
    except ValueError:
        return False
    return until > (now or utcnow())


# ---- Durable queue（复用 agent_queue 的立即事务模式）----


def _cause_for_event(event_type: str) -> str:
    if event_type.startswith("source_fact."):
        return "source_fact"
    if event_type.startswith(("claim.", "profile.", "account.claim")):
        return "claim"
    if event_type.startswith("space.membership"):
        return "membership"
    if event_type.startswith("term."):
        return "term"
    if event_type.startswith("disclosure."):
        return "disclosure"
    return "domain_event"


def schedule_steward_job_for_event(session: Session, event: DomainEvent) -> None:
    """把已追加事件合并到每个受影响空间的活跃 Steward Job。

    领域命令通常仍在同一事务中，因此这里不再开启第二个 SQLite 立即事务；
    只在当前 Session 内更新/新增队列行，由外层命令统一提交。全局事件按空间
    fan-out，但每个 job 仍只读取自己的 scope。card/steward 内部事件由 emit
    调用方过滤，避免执行结果再次触发无限队列。
    """
    if not config.STEWARD_ENABLED or event.type.startswith(("card.", "steward.")):
        return
    session.flush()
    if event.id is None:  # pragma: no cover - autoincrement after flush
        return
    if event.space_id is None:
        space_ids = list(session.scalars(select(FamilySpace.id)))
    else:
        space_ids = [event.space_id]
    cause = _cause_for_event(event.type)
    now = utcnow()
    for space_id in space_ids:
        active = session.scalar(
            select(StewardJob).where(
                StewardJob.space_id == space_id,
                StewardJob.status.in_(STEWARD_ACTIVE_JOB_STATUSES),
            )
        )
        if active is not None:
            if active.trigger_cursor < event.id:
                active.trigger_cursor = event.id
                active.updated_at = now
            continue
        done = session.scalar(
            select(StewardJob)
            .where(
                StewardJob.space_id == space_id,
                StewardJob.status == "succeeded",
                StewardJob.last_event_cursor >= event.id,
            )
            .order_by(StewardJob.id.desc())
        )
        if done is not None:
            continue
        session.add(
            StewardJob(
                space_id=space_id,
                cause=cause,
                trigger_cursor=event.id,
                status="queued",
                attempt=0,
                max_attempts=config.STEWARD_MAX_ATTEMPTS,
                policy_version=POLICY_VERSION,
                checkpoint_json={},
                created_at=now,
                updated_at=now,
            )
        )
    session.flush()


def enqueue_steward_job(
    db: Session,
    *,
    space_id: int,
    cause: str,
    trigger_cursor: int,
    policy_version: str | None = None,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> tuple[StewardJob, bool]:
    """幂等入队。返回 (job, created)。

    - 已有活跃作业且其 trigger_cursor 覆盖本次请求 → 返回该作业（created=False）；
    - 活跃作业存在但水位更旧且 cause 非 admin_rerun → 409（每空间至多一个活跃）；
    - 已 succeeded 且 last_event_cursor ≥ 本次水位（非 admin_rerun）→ 幂等返回
      历史作业，重放零副作用（AC-ST2）；
    - 否则插入 queued 作业。
    """
    _require_enabled()
    if cause not in STEWARD_JOB_CAUSES:
        raise_api_error(422, STEWARD_CAUSE_INVALID, "未知触发原因", detail={"cause": cause})
    attempts = max_attempts if max_attempts is not None else config.STEWARD_MAX_ATTEMPTS
    moment = now or utcnow()
    with _immediate_tx(db):
        active = db.scalar(
            select(StewardJob).where(
                StewardJob.space_id == space_id,
                StewardJob.status.in_(STEWARD_ACTIVE_JOB_STATUSES),
            )
        )
        if active is not None:
            if active.trigger_cursor >= trigger_cursor:
                return active, False
            raise_api_error(
                409,
                STEWARD_JOB_SPACE_BUSY,
                "该空间已有活跃的 Steward Job",
                detail={"job_id": active.id, "trigger_cursor": active.trigger_cursor},
            )
        if cause != "admin_rerun":
            done = db.scalar(
                select(StewardJob)
                .where(
                    StewardJob.space_id == space_id,
                    StewardJob.status == "succeeded",
                    StewardJob.last_event_cursor >= trigger_cursor,
                )
                .order_by(StewardJob.id.desc())
            )
            if done is not None:
                return done, False
        job = StewardJob(
            space_id=space_id,
            cause=cause,
            trigger_cursor=trigger_cursor,
            status="queued",
            attempt=0,
            max_attempts=attempts,
            policy_version=policy_version or POLICY_VERSION,
            checkpoint_json={},
            created_at=moment,
            updated_at=moment,
        )
        db.add(job)
        db.flush()
        return job, True


def require_steward_job(db: Session, job_id: int) -> StewardJob:
    job = db.get(StewardJob, job_id)
    if job is None:
        raise_api_error(404, STEWARD_JOB_NOT_FOUND, "作业不存在", detail={"job_id": job_id})
    return job


def lease_next_steward_job(
    db: Session,
    *,
    leased_by: str,
    space_id: int | None = None,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> StewardJob | None:
    """租赁最早 queued 作业；attempt 每次 lease +1（无作业返回 None）。"""
    _require_enabled()
    ttl = ttl_seconds if ttl_seconds is not None else config.STEWARD_LEASE_TTL_SECONDS
    with _immediate_tx(db):
        stmt = select(StewardJob).where(StewardJob.status == "queued")
        if space_id is not None:
            stmt = stmt.where(StewardJob.space_id == space_id)
        stmt = stmt.order_by(StewardJob.created_at.asc(), StewardJob.id.asc()).limit(1)
        job = db.scalar(stmt)
        if job is None:
            return None
        moment = now or utcnow()
        job.status = "leased"
        job.attempt += 1
        job.leased_by = leased_by
        job.lease_expires_at = moment + timedelta(seconds=ttl)
        job.heartbeat_at = moment
        job.updated_at = moment
        db.flush()
        return job


def heartbeat_steward_job(
    db: Session, job: StewardJob, *, ttl_seconds: int | None = None, now: datetime | None = None
) -> datetime:
    ttl = ttl_seconds if ttl_seconds is not None else config.STEWARD_LEASE_TTL_SECONDS
    with _immediate_tx(db):
        if job.status not in ("leased", "running"):
            raise_api_error(409, STEWARD_JOB_NOT_ACTIVE, "作业不在活跃状态，无法续租")
        moment = now or utcnow()
        expires = moment + timedelta(seconds=ttl)
        job.lease_expires_at = expires
        job.heartbeat_at = moment
        job.updated_at = moment
        db.flush()
        return expires


def settle_steward_job(
    db: Session,
    job: StewardJob,
    *,
    status: str,
    error: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> StewardJob:
    """终态落库（succeeded|failed|expired 仅可从 leased/running 进入）并写领域事件。"""
    if status not in ("succeeded", "failed", "expired"):
        raise_api_error(422, STEWARD_JOB_NOT_ACTIVE, "非法的终态", detail={"status": status})
    moment = now or utcnow()
    with _immediate_tx(db):
        if job.status in ("succeeded", "failed", "expired"):
            raise_api_error(
                409, STEWARD_JOB_NOT_ACTIVE, "作业已是终态", detail={"status": job.status}
            )
        if job.status not in ("leased", "running"):
            raise_api_error(
                409,
                STEWARD_JOB_NOT_ACTIVE,
                "仅 leased/running 可进入终态",
                detail={"status": job.status},
            )
        job.status = status
        job.settled_at = moment
        job.updated_at = moment
        job.error_json = error
        db.flush()
        emit_domain_event(
            db,
            event_type=(
                steward_events.EVENT_STEWARD_JOB_COMPLETED
                if status == "succeeded"
                else steward_events.EVENT_STEWARD_JOB_FAILED
            ),
            aggregate_type=steward_events.AGGREGATE_STEWARD_JOB,
            aggregate_id=job.id,
            payload={
                "job_id": job.id,
                "space_id": job.space_id,
                "cause": job.cause,
                "status": status,
                "attempt": job.attempt,
                **({"error": error} if error else {}),
            },
            space_id=job.space_id,
            actor_account_id=None,
        )
        return job


def reaper_pass(db: Session, *, now: datetime | None = None) -> int:
    """回收过期 lease：attempt 未耗尽回队，耗尽判 expired 终态。返回处理数。"""
    moment = now or utcnow()
    with _immediate_tx(db):
        stale = list(
            db.scalars(
                select(StewardJob).where(
                    StewardJob.status.in_(("leased", "running")),
                    StewardJob.lease_expires_at.is_not(None),
                    StewardJob.lease_expires_at < moment,
                )
            )
        )
        for job in stale:
            exhausted = job.attempt >= job.max_attempts
            outcome = "expired" if exhausted else "queued"
            job.status = outcome
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.updated_at = moment
            if exhausted:
                job.settled_at = moment
                job.error_json = {"code": "STEWARD_LEASE_EXPIRED"}
                emit_domain_event(
                    db,
                    event_type=steward_events.EVENT_STEWARD_JOB_FAILED,
                    aggregate_type=steward_events.AGGREGATE_STEWARD_JOB,
                    aggregate_id=job.id,
                    payload={
                        "job_id": job.id,
                        "space_id": job.space_id,
                        "cause": job.cause,
                        "status": "expired",
                        "attempt": job.attempt,
                    },
                    space_id=job.space_id,
                    actor_account_id=None,
                )
        return len(stale)


# ---- 执行器 ----


def run_steward_job(db: Session, job: StewardJob, *, now: datetime | None = None) -> dict[str, Any]:
    """执行一次 steward 作业（运行 + 结算同一立即事务，崩溃即整体回滚）。"""
    _require_enabled()
    with _immediate_tx(db):
        if job.status not in ("leased", "running"):
            raise_api_error(
                409,
                STEWARD_JOB_NOT_ACTIVE,
                "作业不在可执行状态",
                detail={"status": job.status},
            )
        moment = now or utcnow()
        job.status = "running"
        job.heartbeat_at = moment
        job.updated_at = moment
        summary = _execute_locked(db, job, now=moment)
        job.status = "succeeded"
        job.last_event_cursor = job.trigger_cursor
        job.checkpoint_json = {
            "last_event_cursor": job.trigger_cursor,
            "policy_version": job.policy_version,
            "finding_signatures": summary["finding_signatures"],
            "stats": summary["stats"],
        }
        job.settled_at = moment
        job.updated_at = moment
        db.flush()
        emit_domain_event(
            db,
            event_type=steward_events.EVENT_STEWARD_JOB_COMPLETED,
            aggregate_type=steward_events.AGGREGATE_STEWARD_JOB,
            aggregate_id=job.id,
            payload={
                "job_id": job.id,
                "space_id": job.space_id,
                "cause": job.cause,
                "status": "succeeded",
                "trigger_cursor": job.trigger_cursor,
                "stats": summary["stats"],
            },
            space_id=job.space_id,
            actor_account_id=None,
        )
        return summary


def execute_steward_job(
    db: Session, job: StewardJob, *, now: datetime | None = None
) -> dict[str, Any]:
    """带失败结算的执行包装：异常时把作业标 failed 并原样抛出。"""
    try:
        return run_steward_job(db, job, now=now)
    except Exception as exc:
        settle_steward_job(
            db,
            require_steward_job(db, job.id),
            status="failed",
            error={"code": type(exc).__name__, "message": str(exc)[:500]},
            now=now,
        )
        raise


def _completed_cursor_floor(db: Session, job: StewardJob) -> int:
    """本空间最近一次成功作业的水位（窗口下界；保证跨作业恰好消费一次）。"""
    floor = db.scalar(
        select(StewardJob.last_event_cursor)
        .where(
            StewardJob.space_id == job.space_id,
            StewardJob.status == "succeeded",
            StewardJob.id != job.id,
            StewardJob.last_event_cursor.is_not(None),
        )
        .order_by(StewardJob.last_event_cursor.desc())
        .limit(1)
    )
    return int(floor) if floor is not None else 0


def _execute_locked(db: Session, job: StewardJob, *, now: datetime) -> dict[str, Any]:
    space = db.get(FamilySpace, job.space_id)
    assert space is not None  # FK 保证存在
    stats: dict[str, int] = {
        "events_consumed": 0,
        "derived_recomputed": 0,
        "cards_created": 0,
        "cards_superseded": 0,
        "findings_emitted": 0,
        "cards_expired": 0,
    }
    visible = _space_visible_user_ids(db, space)
    floor = _completed_cursor_floor(db, job)

    # 1. 事件窗口消费 + 本空间派生缓存重算
    touched = _consume_window(db, space, floor=floor, upper=job.trigger_cursor)
    stats["events_consumed"] = len(touched.events)
    stats["derived_recomputed"] = _rebuild_space_derived(db, space, visible)

    # 2. 冲突/缺失检测（只报告）
    findings = _detect_findings(db, space, visible)
    prior_signatures = _prior_finding_signatures(db, job.space_id)
    emitted = _emit_new_findings(db, job, findings, prior_signatures, now=now)
    stats["findings_emitted"] = emitted

    # 3. 推荐矩阵 → 出卡（先重验证旧卡再出新卡，避免陈旧卡阻塞去重）
    stats["cards_superseded"] += _revalidate_active_cards(db, space, now=now)
    stats["cards_created"] = _recommend_cards(db, space, visible, now=now)

    # 4. 惰性过期
    stats["cards_expired"] = action_cards.expire_due_cards(db, space_id=space.id, now=now)

    return {
        "floor_cursor": floor,
        "trigger_cursor": job.trigger_cursor,
        "finding_signatures": sorted(f["signature"] for f in findings),
        "stats": stats,
    }


# ---- 空间可见集合与事实范围（跨空间红线的单点实现）----


def _space_visible_user_ids(db: Session, space: FamilySpace) -> set[int]:
    """当前空间的可见人物：active 成员 ∪ active 节点引用 ∪ owner。"""
    ids: set[int] = {int(space.owner_id)}
    member_rows = db.scalars(
        select(SpaceMember.user_id).where(
            SpaceMember.space_id == space.id, SpaceMember.status == "active"
        )
    )
    ids.update(int(uid) for uid in member_rows)
    ref_rows = db.scalars(
        select(SpaceProfileRef.user_id).where(
            SpaceProfileRef.space_id == space.id, SpaceProfileRef.status == "active"
        )
    )
    ids.update(int(uid) for uid in ref_rows)
    return ids


def _applicable_confirmed_facts(
    db: Session, space: FamilySpace, visible: set[int]
) -> list[SourceFact]:
    """本空间可消费的 confirmed 事实：空间事实 ∪ 双端点均可见的全局事实。"""
    rows = list(
        db.scalars(
            select(SourceFact).where(
                SourceFact.state == "confirmed",
                (SourceFact.space_id == space.id) | (SourceFact.space_id.is_(None)),
            )
        )
    )
    return [row for row in rows if row.subject_user_id in visible and row.object_user_id in visible]


class _WindowEvents:
    """事件窗口消费结果。"""

    __slots__ = ("events", "touched_users")

    def __init__(self, events: list[DomainEvent], touched_users: set[int]) -> None:
        self.events = events
        self.touched_users = touched_users


def _consume_window(db: Session, space: FamilySpace, *, floor: int, upper: int) -> _WindowEvents:
    """读取 (floor, upper] 内与本空间相关的事件；记录 source_fact.* 触及的用户。"""
    events = list(
        db.scalars(
            select(DomainEvent)
            .where(
                DomainEvent.id > floor,
                DomainEvent.id <= upper,
                (DomainEvent.space_id == space.id) | (DomainEvent.space_id.is_(None)),
            )
            .order_by(DomainEvent.id.asc())
        )
    )
    touched: set[int] = set()
    for event in events:
        if not event.type.startswith(SOURCE_FACT_EVENT_PREFIX):
            continue
        # 全局事实事件同样只影响本空间视角（touched 用户随后做本地化失效）
        payload = event.payload or {}
        for key in ("subject_user_id", "object_user_id"):
            value = payload.get(key)
            if isinstance(value, int):
                touched.add(value)
    return _WindowEvents(events=events, touched_users=touched)


def _rebuild_space_derived(db: Session, space: FamilySpace, visible: set[int]) -> int:
    """本空间可见配对的全量 DerivedFact 重算（get_or_compute 自带哈希守卫）。

    采用全量扫描而非增量：中间节点上的事实变化会影响两端点均未触及的路径，
    家族空间规模（几十人）下全量是最不易出错的正确选择；更大规模属后续优化。
    """
    count = 0
    ordered = sorted(visible)
    for viewer in ordered:
        for target in ordered:
            if viewer == target:
                continue
            get_or_compute(db, viewer_user_id=viewer, target_user_id=target, space_id=space.id)
            count += 1
    return count


# ---- 冲突/缺失检测（只报告，不改图）----


def _finding(kind: str, detail: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "kind": kind,
        "detail": detail,
        "signature": hashlib.sha256(f"{kind}:{canonical}".encode()).hexdigest(),
    }


def _detect_findings(db: Session, space: FamilySpace, visible: set[int]) -> list[dict[str, Any]]:
    """确定性检测：
    - conflict/parent_type_clash：同一 (subject,object) 存在多种 confirmed parent 类事实；
    - conflict/parent_cycle：互为 confirmed parent（A→B 且 B→A）；
    - gap/sibling_missing_parents：direct_sibling 无任何共同 confirmed 父母（不虚构）。
    """
    facts = _applicable_confirmed_facts(db, space, visible)
    findings: list[dict[str, Any]] = []

    by_direction: dict[tuple[int, int], set[str]] = {}
    for fact in facts:
        if fact.fact_type in PARENT_FACT_TYPES:
            by_direction.setdefault((fact.subject_user_id, fact.object_user_id), set()).add(
                fact.fact_type
            )
    for (subject_id, object_id), types in sorted(by_direction.items()):
        if len(types) > 1:
            findings.append(
                _finding(
                    "conflict",
                    {
                        "code": "parent_type_clash",
                        "subject_user_id": subject_id,
                        "object_user_id": object_id,
                        "fact_types": sorted(types),
                    },
                )
            )
        reverse = (object_id, subject_id) in by_direction
        if reverse:
            findings.append(
                _finding(
                    "conflict",
                    {
                        "code": "parent_cycle",
                        "pair": sorted([subject_id, object_id]),
                    },
                )
            )

    parents_of: dict[int, set[int]] = {}
    for fact in facts:
        if fact.fact_type in PARENT_FACT_TYPES:
            parents_of.setdefault(fact.object_user_id, set()).add(fact.subject_user_id)
    seen_gap_pairs: set[tuple[int, int]] = set()
    for fact in facts:
        if fact.fact_type != "direct_sibling":
            continue
        a_id, b_id = sorted((fact.subject_user_id, fact.object_user_id))
        pair: tuple[int, int] = (a_id, b_id)
        if pair in seen_gap_pairs:
            continue
        seen_gap_pairs.add(pair)
        shared = parents_of.get(pair[0], set()) & parents_of.get(pair[1], set())
        if not shared:
            findings.append(
                _finding(
                    "gap",
                    {
                        "code": "sibling_missing_parents",
                        "pair": list(pair),
                    },
                )
            )
    return findings


def _prior_finding_signatures(db: Session, space_id: int) -> set[str]:
    raw = db.scalar(
        select(StewardJob.checkpoint_json)
        .where(
            StewardJob.space_id == space_id,
            StewardJob.status == "succeeded",
        )
        .order_by(StewardJob.id.desc())
        .limit(1)
    )
    if not raw:
        return set()
    signatures = raw.get("finding_signatures")
    if not isinstance(signatures, list):
        return set()
    return {str(s) for s in signatures}


def _emit_new_findings(
    db: Session,
    job: StewardJob,
    findings: list[dict[str, Any]],
    prior_signatures: set[str],
    *,
    now: datetime,
) -> int:
    """只对新出现的 finding 签名落领域事件（幂等：重放不再重复发）。"""
    emitted = 0
    for finding in findings:
        if finding["signature"] in prior_signatures:
            continue
        emit_domain_event(
            db,
            event_type=(
                steward_events.EVENT_STEWARD_CONFLICT_DETECTED
                if finding["kind"] == "conflict"
                else steward_events.EVENT_STEWARD_GAP_DETECTED
            ),
            aggregate_type=steward_events.AGGREGATE_STEWARD_JOB,
            aggregate_id=job.id,
            payload={
                "job_id": job.id,
                "space_id": job.space_id,
                "signature": finding["signature"],
                "detail": finding["detail"],
            },
            space_id=job.space_id,
            actor_account_id=None,
        )
        emitted += 1
    return emitted


# ---- 推荐矩阵组装与出卡 ----


def _creation_choices_for(db: Session, space: FamilySpace, fact: SourceFact) -> frozenset[str]:
    """创建选择推导：被创建者（parent 类取 object；对称关系取双方）在本空间的
    active 节点引用映射为本空间 kind；无引用即 no-space（不读其他空间）。"""
    if fact.fact_type in PARENT_FACT_TYPES:
        subjects = [fact.object_user_id]
    else:
        subjects = [fact.subject_user_id, fact.object_user_id]
    choices: set[str] = set()
    for uid in subjects:
        ref = db.scalar(
            select(SpaceProfileRef).where(
                SpaceProfileRef.space_id == space.id,
                SpaceProfileRef.user_id == uid,
                SpaceProfileRef.status == "active",
            )
        )
        if ref is not None:
            choices.add(space.kind)
            break
    if not choices:
        return frozenset({recommendation_matrix.CREATION_NO_SPACE})
    return frozenset(choices)


def _active_member_ids(db: Session, space: FamilySpace, *, include_guest: bool) -> set[int]:
    stmt = select(SpaceMember.user_id).where(
        SpaceMember.space_id == space.id, SpaceMember.status == "active"
    )
    if not include_guest:
        stmt = stmt.where(SpaceMember.role != "guest")
    return {int(uid) for uid in db.scalars(stmt)}


def mutual_disclosure_allowed(db: Session, subject: User, obj: User, space_id: int) -> bool:
    """双方都至少允许一类字段在该空间披露，才视为 mutual disclosure。"""
    return bool(disclosed_categories(db, subject, space_id)) and bool(
        disclosed_categories(db, obj, space_id)
    )


def _pair_inputs(db: Session, space: FamilySpace, fact: SourceFact) -> RecommendationInput:
    """从本空间授权快照确定性组装矩阵输入（cooldown 由调用方按 kind 叠加）。"""
    subject = db.get(User, fact.subject_user_id)
    obj = db.get(User, fact.object_user_id)
    assert subject is not None and obj is not None
    members = _active_member_ids(db, space, include_guest=False)
    share_household = False
    lineage_possible = False
    if space.kind == "household":
        share_household = fact.subject_user_id in members and fact.object_user_id in members
    else:
        subj_in = fact.subject_user_id in members
        obj_in = fact.object_user_id in members
        lineage_possible = subj_in != obj_in  # 恰一端是成员：另一端可申请加入
    return RecommendationInput(
        fact_type=fact.fact_type,
        fact_state=fact.state,
        subject_identity_confirmed=(subject.profile_status == "identity_confirmed"),
        object_identity_confirmed=(obj.profile_status == "identity_confirmed"),
        creation_choices=_creation_choices_for(db, space, fact),
        mutual_disclosure_allowed=(
            mutual_disclosure_allowed(db, subject, obj, space.id)
            if fact.fact_type == "partner"
            else False
        ),  # partner 需要双方在本空间各自明确允许披露
        share_household_membership=share_household,
        lineage_request_possible=lineage_possible,
        in_cooldown=False,
    )


def _evidence_json(fact: SourceFact, inp: RecommendationInput) -> dict[str, Any]:
    """证据快照：仅含 fact 指纹与矩阵输入标量（无 masked 原值、无冷却态）。"""
    return {
        "primary_fact_id": fact.id,
        "facts": [{"id": fact.id, "type": fact.fact_type, "revision": fact.revision}],
        "inputs": {
            "subject_identity_confirmed": inp.subject_identity_confirmed,
            "object_identity_confirmed": inp.object_identity_confirmed,
            "creation_choices": sorted(inp.creation_choices),
            "mutual_disclosure_allowed": inp.mutual_disclosure_allowed,
            "share_household_membership": inp.share_household_membership,
            "lineage_request_possible": inp.lineage_request_possible,
        },
    }


_REASON_TEMPLATES: dict[tuple[str, str], str] = {
    ("household_link", "spouse"): "{a} 与 {b} 已确认为配偶，可以共同创建一个家庭空间。",
    ("household_link", "partner"): (
        "{a} 与 {b} 已相互确认伴侣关系并允许披露，可以共同创建一个" "家庭空间。"
    ),
    ("household_link", "guardian"): "{a} 是 {b} 的监护人，可以把 {b} 加入家庭空间。",
    ("household_link", "direct_sibling"): "{a} 与 {b} 已确认为兄弟姐妹，可以加入同一个家庭空间。",
    ("lineage_request", "spouse"): "{a} 可以申请加入 {b} 所在的家族空间。",
}


def _reason_text(kind: str, fact: SourceFact, a: User, b: User) -> str:
    template = _REASON_TEMPLATES.get((kind, fact.fact_type))
    if template is None:
        relation = "亲属"
        if fact.fact_type in PARENT_FACT_TYPES:
            relation = "亲子"
        template = "{a} 与 {b} 已确认" + relation + "关系，可以建立家庭空间关联。"
    return template.format(a=a.name, b=b.name)


def _materialize_action(
    db: Session,
    space: FamilySpace,
    fact: SourceFact,
    action: str,
    inp: RecommendationInput,
    *,
    now: datetime,
) -> bool:
    kind = _ACTION_TO_KIND[action]
    subject = db.get(User, fact.subject_user_id)
    assert subject is not None
    account_id = db.scalar(select(Account.id).where(Account.user_id == subject.id))
    if account_id is None:
        return False
    if kind_in_cooldown(db, space_id=space.id, account_id=int(account_id), kind=kind, now=now):
        return False
    proposed: dict[str, Any] = {"action": action}
    if action == ACTION_REQUEST_LINEAGE:
        proposed["space_id"] = space.id
    card, _outcome = action_cards.create_card(
        db,
        kind=kind,
        space_id=space.id,
        recipient_account_id=int(account_id),
        subject_user_id=fact.subject_user_id,
        object_user_id=fact.object_user_id,
        evidence_json=_evidence_json(fact, inp),
        proposed_action_json=proposed,
        reason_text=_reason_text(kind, fact, subject, db.get(User, fact.object_user_id)),  # type: ignore[arg-type]
        now=now,
    )
    return card is not None


def _recommend_cards(db: Session, space: FamilySpace, visible: set[int], *, now: datetime) -> int:
    created = 0
    for fact in _applicable_confirmed_facts(db, space, visible):
        inp = _pair_inputs(db, space, fact)
        outcome = evaluate_recommendation(inp)
        if not outcome.eligible:
            continue
        for action in outcome.actions:
            if _materialize_action(db, space, fact, action, inp, now=now):
                created += 1
    return created


def _revalidate_active_cards(db: Session, space: FamilySpace, *, now: datetime) -> int:
    """活动卡复核：证据失效 → 仅取代；输入变化 → 经 create_card 换发新版（AC-ST3）。"""
    superseded = 0
    for card in action_cards.active_cards_in_space(db, space.id):
        primary_id = card.evidence_json.get("primary_fact_id")
        fact = db.get(SourceFact, int(primary_id)) if isinstance(primary_id, int) else None
        if fact is None or fact.state != "confirmed":
            action_cards.supersede_card(db, card, reason="evidence_invalidated", now=now)
            superseded += 1
            continue
        inp = _pair_inputs(db, space, fact)
        outcome = evaluate_recommendation(inp)
        wanted = {_ACTION_TO_KIND[a] for a in outcome.actions}
        if not outcome.eligible or card.kind not in wanted:
            action_cards.supersede_card(db, card, reason="eligibility_lost", now=now)
            superseded += 1
            continue
        fresh = action_cards.compute_evidence_hash(_evidence_json(fact, inp))
        if fresh != card.evidence_hash:
            for action in outcome.actions:
                if _ACTION_TO_KIND[action] == card.kind:
                    _materialize_action(db, space, fact, action, inp, now=now)
    return superseded


__all__ = [
    "ACTION_SUPERSEDE",
    "POLICY_VERSION",
    "PROJECTION_KEY_PREFIXES",
    "enqueue_steward_job",
    "execute_steward_job",
    "heartbeat_steward_job",
    "kind_in_cooldown",
    "lease_next_steward_job",
    "mutual_disclosure_allowed",
    "put_projection",
    "reaper_pass",
    "require_steward_job",
    "run_steward_job",
    "schedule_steward_job_for_event",
    "set_kind_cooldown",
    "settle_steward_job",
]
