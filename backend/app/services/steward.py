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

事务模型：一致快照在事务外计算，完整目标在短事务中保存；最终只切换发布指针、
消费水位和交付门控。确定性称谓按目标独立交付，完成后登记模型辅助批次。
HTTP 由 maintenance 的事务外受限线程执行；批次/attempt 行保留发送与未知结果
恢复合同（见 services/steward_assist.py）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import config
from app.errors import (
    BEHAVIOR_PROJECTION_DISABLED,
    CARD_INVALID_TRANSITION,
    STEWARD_CAUSE_INVALID,
    STEWARD_DISABLED,
    STEWARD_JOB_NOT_ACTIVE,
    STEWARD_JOB_NOT_FOUND,
    STEWARD_JOB_SPACE_BUSY,
    STEWARD_LEASE_STALE,
    extract_api_error,
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
    StewardSpaceSchedule,
)
from app.models.user import User
from app.models.v2_foundation import DomainEvent
from app.services import (
    action_cards,
    person_identity,
    recommendation_matrix,
    steward_events,
)
from app.services.action_cards import ACTION_SUPERSEDE
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
logger = logging.getLogger(__name__)

POLICY_VERSION = config.POLICY_VERSION

_ACTION_TO_KIND: dict[str, str] = {
    ACTION_CREATE_HOUSEHOLD: "household_link",
    ACTION_REQUEST_LINEAGE: "lineage_request",
}

# 行为投影键白名单前缀（红线：泛行为监控字段一律拒绝）
PROJECTION_KEY_PREFIXES = (
    "card_cooldown:",
    "correction_preference:",
    "term_usage:",
    "kinship_recommendation_dismissed:",
)

# ---- 安全错误分类（09-11 R3/F16）----
# 只落白名单分类码；异常原文/SQL 参数/堆栈永不进入 error_json、领域事件或日志。
ERROR_TRANSIENT_DB_LOCK = "STEWARD_TRANSIENT_DB_LOCK"
ERROR_TRANSIENT_TIMEOUT = "STEWARD_TRANSIENT_TIMEOUT"
ERROR_EXECUTION_FAILED = "STEWARD_EXECUTION_FAILED"
ERROR_DB_OPERATIONAL = "STEWARD_DB_OPERATIONAL"
# 可重试的 SQLite 瞬时锁冲突特征（busy_timeout 耗尽 / 死锁类暂时资源错误）
_RETRYABLE_LOCK_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
)


def classify_execution_error(exc: Exception) -> tuple[str, bool]:
    """把执行异常分类为 (安全错误码, 是否可重试)。

    可重试 = 数据库锁冲突/暂时超时（有限退避后重试）；确定性 = 输入/权限类
    业务错误（带 API 错误码，fail-closed 进入 failed 终态）与其余未知异常
    （未知异常一律按确定性处理：不无限重试掩盖真实缺陷）。
    """
    from app.services.steward_runtime import RuntimeStopping

    if isinstance(exc, RuntimeStopping):
        return "worker_stopped", True
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        if any(marker in message for marker in _RETRYABLE_LOCK_MARKERS):
            return ERROR_TRANSIENT_DB_LOCK, True
        return ERROR_DB_OPERATIONAL, False
    if isinstance(exc, TimeoutError):
        return ERROR_TRANSIENT_TIMEOUT, True
    api_error = extract_api_error(getattr(exc, "detail", None))
    if api_error is not None:
        # 输入/权限类确定性错误：保留白名单 API 错误码（无原文参数）
        return str(api_error.get("code", ERROR_EXECUTION_FAILED)), False
    return ERROR_EXECUTION_FAILED, False


def _lease_stale(job: StewardJob, *, worker_id: str | None, now: datetime) -> bool:
    """租约栅栏：owner 不匹配或 lease 已过期（旧执行者不得覆盖新租约）。"""
    if worker_id is not None and job.leased_by != worker_id:
        return True
    deadline = job.lease_expires_at
    return deadline is None or deadline <= now


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
    if not config.BEHAVIOR_PROJECTION_ENABLED:
        raise_api_error(503, BEHAVIOR_PROJECTION_DISABLED, "行为投影功能未开启")
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
) -> BehaviorProjection | None:
    """卡片 dismissed 后的同 kind 冷却（ST-3：相同证据不重复骚扰的时间维度）。"""
    if kind not in CARD_KINDS:
        raise_api_error(422, CARD_INVALID_TRANSITION, "未知卡片种类", detail={"kind": kind})
    if not config.BEHAVIOR_PROJECTION_ENABLED:
        return None
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
    if not config.BEHAVIOR_PROJECTION_ENABLED:
        return False
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


def rebuild_behavior_projections(
    session: Session,
    *,
    space_id: int,
    account_id: int | None = None,
    now: datetime | None = None,
) -> int:
    """Rebuild the allow-listed behavior projection from domain events.

    Projection rows are a cache, not an authority.  Replaying only explicit
    card/term events makes the result deterministic and avoids persisting
    keyboard, click, hover, or dwell-time telemetry.
    """
    if not config.BEHAVIOR_PROJECTION_ENABLED:
        return 0
    event_accounts = {
        int(value)
        for value in session.scalars(
            select(DomainEvent.actor_account_id).where(
                DomainEvent.space_id == space_id,
                DomainEvent.actor_account_id.is_not(None),
                DomainEvent.type.in_(
                    ("card.dismissed", "term.personal_updated", "term.usage_recorded")
                ),
            )
        )
        if value is not None
    }
    if account_id is not None:
        account_ids = {account_id}
        session.execute(
            delete(BehaviorProjection).where(
                BehaviorProjection.space_id == space_id,
                BehaviorProjection.account_id == account_id,
            )
        )
    else:
        account_ids = event_accounts
        session.execute(delete(BehaviorProjection).where(BehaviorProjection.space_id == space_id))
    if not account_ids:
        return 0

    events = list(
        session.scalars(
            select(DomainEvent)
            .where(
                DomainEvent.space_id == space_id,
                DomainEvent.actor_account_id.in_(account_ids),
                DomainEvent.type.in_(
                    ("card.dismissed", "term.personal_updated", "term.usage_recorded")
                ),
            )
            .order_by(DomainEvent.id.asc())
        )
    )
    rebuilt = 0
    usage_counts: dict[tuple[int, str], int] = {}
    for event in events:
        actor = event.actor_account_id
        if actor is None:
            continue
        payload = event.payload or {}
        if event.type == "card.dismissed":
            kind = payload.get("kind")
            if isinstance(kind, str) and kind in CARD_KINDS:
                until = event.created_at + timedelta(days=config.STEWARD_COOLDOWN_DAYS)
                put_projection(
                    session,
                    space_id=space_id,
                    account_id=actor,
                    projection_key=f"card_cooldown:{kind}",
                    value={"until": until.isoformat()},
                    now=now or event.created_at,
                )
                rebuilt += 1
        elif event.type == "term.personal_updated":
            concept = payload.get("concept_code")
            entry_id = payload.get("entry_id")
            if isinstance(concept, str) and isinstance(entry_id, int):
                put_projection(
                    session,
                    space_id=space_id,
                    account_id=actor,
                    projection_key=f"correction_preference:{concept}",
                    value={"entry_id": entry_id, "updated_at": event.created_at.isoformat()},
                    now=now or event.created_at,
                )
                rebuilt += 1
        elif event.type == "term.usage_recorded":
            concept = payload.get("concept_code")
            if isinstance(concept, str):
                key = (actor, concept)
                usage_counts[key] = usage_counts.get(key, 0) + 1
                put_projection(
                    session,
                    space_id=space_id,
                    account_id=actor,
                    projection_key=f"term_usage:{concept}",
                    value={"count": usage_counts[key], "updated_at": event.created_at.isoformat()},
                    now=now or event.created_at,
                )
                rebuilt += 1
    return rebuilt


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
    只在当前 Session 内更新/新增队列行，由外层命令统一提交。空间作用域由
    ``domain_events.resolve_event_space_ids`` 统一权威解析：事件自身空间 ∪
    payload 空间列表（桥接两侧）→ 全局人物事件按 active membership/ref/桥接受权
    范围收敛，绝不广播到全部空间。card/steward 内部事件由 emit 调用方过滤。
    """
    if not config.STEWARD_ENABLED or event.type.startswith(("card.", "steward.")):
        return
    session.flush()
    if event.id is None:  # pragma: no cover - autoincrement after flush
        return
    from app.services.domain_events import resolve_event_space_ids

    space_ids = resolve_event_space_ids(session, event)
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
    retry_of_job_id: int | None = None,
) -> tuple[StewardJob, bool]:
    """幂等入队。返回 (job, created)。

    - 已有活跃作业且其 trigger_cursor 覆盖本次请求 → 返回该作业（created=False）；
    - 活跃作业存在但水位更旧且 cause 非 admin_rerun → 409（每空间至多一个活跃）；
    - 已 succeeded 且 last_event_cursor ≥ 本次水位（非 admin_rerun）→ 幂等返回
      历史作业，重放零副作用（AC-ST2）；
    - 否则插入 queued 作业（retry_of_job_id 链接被关联的历史作业，如人工重跑）。
    """
    _require_enabled()
    if cause not in STEWARD_JOB_CAUSES:
        raise_api_error(422, STEWARD_CAUSE_INVALID, "未知触发原因", detail={"cause": cause})
    attempts = max_attempts if max_attempts is not None else config.STEWARD_MAX_ATTEMPTS
    moment = now or utcnow()
    with _immediate_tx(db):
        return _enqueue_core_job_locked(
            db,
            space_id=space_id,
            cause=cause,
            trigger_cursor=trigger_cursor,
            policy_version=policy_version,
            max_attempts=attempts,
            now=moment,
            retry_of_job_id=retry_of_job_id,
        )


def _enqueue_core_job_locked(
    db: Session,
    *,
    space_id: int,
    cause: str,
    trigger_cursor: int,
    policy_version: str | None = None,
    max_attempts: int | None = None,
    now: datetime,
    respect_succeeded_shortcircuit: bool = True,
    retry_of_job_id: int | None = None,
) -> tuple[StewardJob, bool]:
    """canonical enqueue 的锁内实现（调用方必须已持有立即事务写锁）。

    - integrity_scan（周期扫描）不走 succeeded 水位短路：cursor 相同也允许到期
      检查执行（R2：扫描不被历史 succeeded 幂等短路）；
    - admin_rerun 人工重跑：绕过短路并链接被重跑的历史作业（retry_of_job_id）。
    """
    attempts = max_attempts if max_attempts is not None else config.STEWARD_MAX_ATTEMPTS
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
    if respect_succeeded_shortcircuit and cause not in {"admin_rerun", "integrity_scan"}:
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
        created_at=now,
        updated_at=now,
        retry_of_job_id=retry_of_job_id,
    )
    db.add(job)
    db.flush()
    if cause == "admin_rerun":
        # Reserve one auditable opportunity; scans/restarts cannot reset past
        # attempts. The admin endpoint also enforces its space-level cooldown.
        from app.models.steward import StewardRetryBudget

        budgets = db.scalars(
            select(StewardRetryBudget)
            .where(
                StewardRetryBudget.space_id == space_id,
                StewardRetryBudget.scope != "delivery",
                (StewardRetryBudget.exhausted.is_(True))
                | (StewardRetryBudget.retry_after.is_not(None)),
                (StewardRetryBudget.manual_retry_at.is_(None))
                | (
                    StewardRetryBudget.manual_retry_at
                    <= now - timedelta(seconds=config.STEWARD_RERUN_COOLDOWN_SECONDS)
                ),
            )
            .order_by(StewardRetryBudget.updated_at.desc())
            .limit(16)
        ).all()
        for budget in budgets:
            budget.max_attempts = max(budget.max_attempts, budget.attempts + 1)
            budget.exhausted, budget.retry_after = False, None
            budget.manual_grants += 1
            budget.manual_retry_at, budget.updated_at = now, now
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
    """租赁最早 queued 作业；attempt 每次 lease +1（无作业返回 None）。

    available_at 未到的退避重试作业不可租（09-11 R3：有限退避）；lease 同时
    固定本次执行水位上界（checkpoint_json.execution_cursor）：运行期间到来的
    更高事件水位只更新 trigger_cursor 请求后继工作，结算不得宣告未处理水位。
    """
    _require_enabled()
    ttl = ttl_seconds if ttl_seconds is not None else config.STEWARD_LEASE_TTL_SECONDS
    from app.services.steward_delivery import terminology_blocks_core
    from app.services.steward_overlay import active_leases

    with _immediate_tx(db):
        db.expire_all()
        stmt = select(StewardJob).where(StewardJob.status == "queued")
        if space_id is not None:
            stmt = stmt.where(StewardJob.space_id == space_id)
        moment = now or utcnow()
        active_count = (
            db.scalar(
                select(func.count())
                .select_from(StewardJob)
                .where(
                    StewardJob.status.in_(("leased", "running")),
                    StewardJob.lease_expires_at > moment,
                )
            )
            or 0
        )
        if active_count + active_leases(db) >= config.STEWARD_MAX_CONCURRENT_JOBS:
            return None
        # 退避未到期的作业暂不可租
        stmt = stmt.where((StewardJob.available_at.is_(None)) | (StewardJob.available_at <= moment))
        # Finish the published generation's bounded terminology delivery before
        # leasing its presentation successor. Other spaces remain eligible;
        # changed structure/config immediately releases this local barrier.
        stmt = stmt.where(~terminology_blocks_core(now=moment))
        stmt = stmt.order_by(StewardJob.created_at.asc(), StewardJob.id.asc()).limit(1)
        job = db.scalar(stmt)
        if job is None:
            return None
        job.status = "leased"
        job.attempt += 1
        job.leased_by = leased_by
        job.lease_expires_at = moment + timedelta(seconds=ttl)
        job.heartbeat_at = moment
        job.updated_at = moment
        checkpoint = dict(job.checkpoint_json or {})
        checkpoint["execution_cursor"] = job.trigger_cursor
        job.checkpoint_json = checkpoint
        db.flush()
        return job


def heartbeat_steward_job(
    db: Session,
    job: StewardJob,
    *,
    worker_id: str | None = None,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
    expected_attempt: int | None = None,
) -> datetime:
    from app.services import steward_pipeline

    binding = steward_pipeline.binding_for(
        job, worker_id=worker_id, expected_attempt=expected_attempt
    )
    return steward_pipeline.heartbeat(
        db,
        binding,
        ttl=ttl_seconds if ttl_seconds is not None else config.STEWARD_LEASE_TTL_SECONDS,
        now=now,
    )


def settle_steward_job(
    db: Session,
    job: StewardJob,
    *,
    status: str,
    error: dict[str, Any] | None = None,
    now: datetime | None = None,
    worker_id: str | None = None,
    expected_attempt: int | None = None,
    error_code: str | None = None,
) -> StewardJob:
    """终态落库（succeeded|failed|expired 仅可从 leased/running 进入）并写领域事件。

    租约栅栏（F17）：验证 job id + attempt + lease owner + lease deadline——
    旧执行者用过期租约/旧 attempt 结算被拒（409 STEWARD_LEASE_STALE），
    不覆盖新 lease 持有者的工作。
    """
    if status not in ("succeeded", "failed", "expired"):
        raise_api_error(422, STEWARD_JOB_NOT_ACTIVE, "非法的终态", detail={"status": status})
    observed_attempt = job.attempt if expected_attempt is None else expected_attempt
    observed_owner = job.leased_by if worker_id is None else worker_id
    moment = now or utcnow()
    with _immediate_tx(db):
        db.refresh(job)
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
        if job.attempt != observed_attempt:
            raise_api_error(
                409,
                STEWARD_LEASE_STALE,
                "attempt 与当前租约不一致，结算被拒绝",
                detail={"expected_attempt": expected_attempt, "attempt": job.attempt},
            )
        if _lease_stale(job, worker_id=observed_owner, now=moment):
            raise_api_error(409, STEWARD_LEASE_STALE, "租约已过期或易主，结算被拒绝")
        from app.models.steward import StewardGeneration
        from app.services import steward_pipeline

        generation = db.scalar(
            select(StewardGeneration)
            .where(
                StewardGeneration.job_id == job.id,
                StewardGeneration.manifest_sealed.is_(True),
                StewardGeneration.status == "running",
            )
            .order_by(StewardGeneration.id.desc())
            .limit(1)
        )
        if generation is not None:
            steward_pipeline.require_generation(
                db,
                steward_pipeline.Binding(
                    job.id,
                    job.space_id,
                    observed_owner,
                    observed_attempt,
                ),
                generation.id,
                now=moment,
            )
            if status == "succeeded":
                raise_api_error(409, STEWARD_JOB_NOT_ACTIVE, "分代作业必须通过完整发布栅栏结算")
            generation.status = "failed" if status == "failed" else "superseded"
            generation.error_code, generation.updated_at = error_code or status, moment
        job.status = status
        job.settled_at = moment
        job.updated_at = moment
        job.error_json = error
        safe_code = error_code or (str(error.get("code")) if error and error.get("code") else None)
        job.error_code = safe_code
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
        db.expire_all()
        stale = list(
            db.scalars(
                select(StewardJob)
                .where(
                    StewardJob.status.in_(("leased", "running")),
                    StewardJob.lease_expires_at.is_not(None),
                    StewardJob.lease_expires_at <= moment,
                )
                .order_by(StewardJob.id)
                .limit(32)
            )
        )
        for job in stale:
            from app.models.steward import StewardGeneration

            for generation in db.scalars(
                select(StewardGeneration).where(
                    StewardGeneration.job_id == job.id,
                    StewardGeneration.status == "running",
                    StewardGeneration.lease_attempt == job.attempt,
                    StewardGeneration.lease_owner == job.leased_by,
                )
            ).all():
                generation.status = "superseded"
                generation.error_code = "lease_expired"
                generation.updated_at = moment
            exhausted = job.attempt >= job.max_attempts
            outcome = "expired" if exhausted else "queued"
            job.status = outcome
            job.lease_expires_at = None
            job.leased_by = None
            job.heartbeat_at = None
            job.updated_at = moment
            if exhausted:
                job.settled_at = moment
                job.error_json = {"code": "STEWARD_LEASE_EXPIRED"}
                job.error_code = "STEWARD_LEASE_EXPIRED"
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


# ---- 周期扫描与追补（09-11 R2；调度表不改事实，只触发 canonical enqueue）----


def current_event_watermark(db: Session) -> int:
    """domain_events 全局水位（追加单调）；各空间作业以此为其窗口上界候选。"""
    return int(db.scalar(select(func.max(DomainEvent.id))) or 0)


# 内部调用别名（scan 路径）
_current_watermark = current_event_watermark


def scan_due_spaces(db: Session, *, limit: int | None = None, now: datetime | None = None) -> int:
    # Note: 扫描/重试/租约栅栏合同 —
    # 见 .agent-notes/implemented/architecture/2026-09-11-steward-scheduling-lease-fencing.md
    """周期扫描：选择到期空间并经 canonical enqueue 合同登记核心作业。

    - 短 BEGIN IMMEDIATE + 单写者锁：多进程/多 listener 并发扫描不会重复登记
      活跃作业（每空间至多一个活跃作业由锁内检查 + partial unique index 兜底）；
    - 追补：调度行缺失（首次启用/重新启用）或 policy_version 变化 → 该空间被
      置为立即到期，本轮即登记追补作业；
    - cursor 与上次相同也照样触发到期检查（integrity_scan 不走 succeeded 水位
      幂等短路——到期卡片检查必须持续运行，不能只依据 max(event.id) 宣告已算）；
    - 扫描绝不直接写 SourceFact / 视图 / 卡片，只登记作业。
    """
    _require_enabled()
    moment = now or utcnow()
    max_jobs = limit if limit is not None else config.STEWARD_SCAN_MAX_JOBS_PER_TICK
    interval = timedelta(seconds=config.STEWARD_SCAN_INTERVAL_SECONDS)
    with _immediate_tx(db):
        space_ids = list(db.scalars(select(FamilySpace.id)).all())
        rows = {row.space_id: row for row in db.scalars(select(StewardSpaceSchedule)).all()}
        for space_id in space_ids:
            row = rows.get(space_id)
            if row is None:
                db.add(
                    StewardSpaceSchedule(
                        space_id=space_id,
                        next_scan_at=moment,
                        last_scheduled_cursor=0,
                        policy_version=POLICY_VERSION,
                        updated_at=moment,
                    )
                )
            elif row.policy_version != POLICY_VERSION:
                row.policy_version = POLICY_VERSION
                row.next_scan_at = moment
                row.updated_at = moment
        db.flush()
        due = list(
            db.scalars(
                select(StewardSpaceSchedule)
                .where(StewardSpaceSchedule.next_scan_at <= moment)
                .order_by(StewardSpaceSchedule.next_scan_at.asc())
                .limit(max_jobs)
            )
        )
        enqueued = 0
        for schedule in due:
            watermark = _current_watermark(db)
            active = db.scalar(
                select(StewardJob).where(
                    StewardJob.space_id == schedule.space_id,
                    StewardJob.status.in_(STEWARD_ACTIVE_JOB_STATUSES),
                )
            )
            if active is None:
                _job, created = _enqueue_core_job_locked(
                    db,
                    space_id=schedule.space_id,
                    cause="integrity_scan",
                    trigger_cursor=watermark,
                    now=moment,
                    respect_succeeded_shortcircuit=False,
                )
                if created:
                    enqueued += 1
            schedule.last_scheduled_cursor = watermark
            schedule.next_scan_at = moment + interval
            schedule.updated_at = moment
        return enqueued


# ---- 执行器 ----


def run_steward_job(
    db: Session,
    job: StewardJob,
    *,
    now: datetime | None = None,
    worker_id: str | None = None,
    expected_attempt: int | None = None,
    drain_delivery: bool = True,
) -> dict[str, Any]:
    """Run detached computation, stage complete targets, then atomically publish."""
    from app.services import steward_delivery, steward_pipeline
    from app.services.steward_snapshot import SnapshotChanged

    _require_enabled()
    binding = steward_pipeline.binding_for(
        job, worker_id=worker_id, expected_attempt=expected_attempt
    )
    raw = db.connection().connection.dbapi_connection
    if isinstance(raw, sqlite3.Connection) and raw.in_transaction:
        raise RuntimeError("steward requires a clean session without pending writes")
    db.rollback()
    current = steward_pipeline.begin_job(db, binding, now=now)
    raw_upper = (current.checkpoint_json or {}).get("execution_cursor")
    upper = (
        current.trigger_cursor
        if not isinstance(raw_upper, int)
        else min(raw_upper, current.trigger_cursor)
    )
    heartbeat = _LeaseHeartbeat(binding=binding, bind=db.get_bind())
    try:
        heartbeat.start()
        summary = _execute_locked(db, current, now=now or utcnow(), upper=upper)
        _publish_success(
            db,
            current,
            exec_upper=upper,
            summary=summary,
            worker_id=binding.owner,
            expected_attempt=binding.attempt,
        )
    except Exception as exc:
        code, retryable = classify_execution_error(exc)
        if isinstance(exc, SnapshotChanged):
            code, retryable = "input_changed", False
        try:
            steward_pipeline.record_failure(db, binding, error_code=code, retryable=retryable)
        except Exception as failure:
            logger.warning(
                "steward failure settlement deferred job=%s error=%s",
                binding.job_id,
                type(failure).__name__,
            )
        raise
    finally:
        heartbeat.stop()
    if drain_delivery:
        try:
            delivered = steward_delivery.drain(
                bind=db.get_bind(), generation_id=int(summary["generation_id"]), limit=64
            )
        except Exception as exc:
            # Publication already committed. Delivery recovery is independent;
            # a lock or poison object cannot turn core success into a failure.
            logger.warning(
                "steward delivery deferred generation=%s error=%s",
                summary["generation_id"],
                type(exc).__name__,
            )
            delivered = {"delivery_failed": 1}
        for key, count in delivered.items():
            if key in summary["stats"]:
                summary["stats"][key] += count
        summary["delivery"] = delivered
    db.expire_all()
    return summary


class _LeaseHeartbeat:
    """Independent, expected-attempt heartbeat; no shared Session or ORM object."""

    def __init__(self, *, binding: Any, bind: Any) -> None:
        self._binding = binding
        self._bind = bind
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lost = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"steward-heartbeat-{self._binding.job_id}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        from app.services import steward_pipeline

        interval = max(config.STEWARD_LEASE_TTL_SECONDS / 3.0, 0.25)
        while not self._stop.wait(interval):
            try:
                with Session(bind=self._bind, expire_on_commit=False) as session:
                    steward_pipeline.heartbeat(
                        session, self._binding, ttl=config.STEWARD_LEASE_TTL_SECONDS
                    )
            except Exception as exc:
                self.lost = True
                logger.warning(
                    "steward lease heartbeat stopped job=%s error=%s",
                    self._binding.job_id,
                    type(exc).__name__,
                )
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Coordinator completion must include its last heartbeat write.
            # Runtime shutdown supplies the outer bounded wait and returns
            # False if a database operation has not stopped by its deadline.
            self._thread.join()


def _publish_success(
    db: Session,
    job: StewardJob,
    *,
    exec_upper: int,
    summary: dict[str, Any],
    worker_id: str | None,
    expected_attempt: int | None = None,
) -> None:
    from app.services import steward_pipeline

    binding = steward_pipeline.binding_for(
        job, worker_id=worker_id, expected_attempt=expected_attempt
    )
    steward_pipeline.publish(db, binding, summary=summary, upper=exec_upper)


def execute_steward_job(
    db: Session,
    job: StewardJob,
    *,
    now: datetime | None = None,
    worker_id: str | None = None,
    expected_attempt: int | None = None,
    drain_delivery: bool = True,
) -> dict[str, Any]:
    """Recover only the exact observed binding, including failures before snapshot."""
    from app.services import steward_pipeline

    binding = steward_pipeline.binding_for(
        job, worker_id=worker_id, expected_attempt=expected_attempt
    )
    try:
        return run_steward_job(
            db,
            job,
            now=now,
            worker_id=worker_id,
            expected_attempt=expected_attempt,
            drain_delivery=drain_delivery,
        )
    except Exception as exc:
        code, retryable = classify_execution_error(exc)
        if code not in (STEWARD_LEASE_STALE, STEWARD_JOB_NOT_ACTIVE):
            steward_pipeline.record_failure(db, binding, error_code=code, retryable=retryable)
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


def _execute_locked(
    db: Session, job: StewardJob, *, now: datetime, upper: int | None = None
) -> dict[str, Any]:
    """Compatibility entry for execution fault injection; owns no write lock."""
    from app.services import steward_pipeline

    return steward_pipeline.execute(
        db, job, now=now, upper=job.trigger_cursor if upper is None else upper
    )


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


def _confirmed_facts_brief(
    db: Session, space: FamilySpace, visible: set[int]
) -> list[dict[str, Any]]:
    """候选辅助的 prompt 输入（B6 白名单）：仅 display name、fact_type、用户 id。

    绝不含 masked 原值、健康/住址等高敏感类别、私人 Session/Memory 内容；
    明文不落库（审计只存 sha256 摘要）。
    """
    brief: list[dict[str, Any]] = []
    for fact in _applicable_confirmed_facts(db, space, visible):
        subject = db.get(User, fact.subject_user_id)
        obj = db.get(User, fact.object_user_id) if fact.object_user_id else None
        if subject is None:
            continue
        brief.append(
            {
                "fact_type": fact.fact_type,
                "subject_user_id": fact.subject_user_id,
                "subject_name": subject.name,
                "object_user_id": fact.object_user_id,
                "object_name": obj.name if obj else None,
            }
        )
    return brief


class _WindowEvents:
    """事件窗口消费结果。"""

    __slots__ = ("events", "touched_users")

    def __init__(self, events: list[DomainEvent], touched_users: set[int]) -> None:
        self.events = events
        self.touched_users = touched_users


def _consume_window(db: Session, space: FamilySpace, *, floor: int, upper: int) -> _WindowEvents:
    """读取 (floor, upper] 内与本空间相关的事件；记录 source_fact.* 触及的用户。"""
    # Note: 事件窗口复用统一空间解析合同 — 见
    # .agent-notes/implemented/bug-fix/2026-09-12-steward-event-window-scope.md
    candidates = list(
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
    # 调度入口与窗口消费共用同一空间解析合同，避免把无关全局事件计入
    # events_consumed 或在未来复用 touched_users 时越过租户边界。Memory/RAG
    # 由各自索引流程管理，从未登记 Steward 作业，也不应进入该窗口统计。
    from app.services.domain_events import resolve_event_space_ids

    events = [
        event
        for event in candidates
        if not event.type.startswith(("memory.", "rag."))
        and space.id in resolve_event_space_ids(db, event)
    ]
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
    """本空间可见配对的 DerivedFact 重算（evidence_hash 指纹守卫）。

    09-13 短事务形态：``compute_pair`` 只做路径解析（纯读取，无写锁）；
    每 STEWARD_DERIVED_COMMIT_CHUNK 对在单个立即事务内 ``apply_pair_result``
    落库并提交。分块提交期间其他写者（登录/lease/maintenance）可在块间
    获得写锁——这正是本改造的可用性目标；代价是全量扫描不构成单一致快照，
    由指纹守卫 + 后继扫描收敛（漂移输入下一轮自然重算）。
    """
    from app.services.derived_facts import apply_pair_result, compute_pair

    count = 0
    chunk_size = max(config.STEWARD_DERIVED_COMMIT_CHUNK, 1)
    buffer: list[tuple[Any, Any, Any]] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        with _immediate_tx(db):
            for resolution, row, current_hash in buffer:
                apply_pair_result(db, resolution, row, current_hash)
        buffer.clear()

    ordered = sorted(visible)
    for viewer in ordered:
        for target in ordered:
            if viewer == target:
                continue
            buffer.append(
                compute_pair(db, viewer_user_id=viewer, target_user_id=target, space_id=space.id)
            )
            count += 1
            if len(buffer) >= chunk_size:
                flush_buffer()
    flush_buffer()
    return count


# ---- 冲突/缺失检测（只报告，不改图）----


def _finding(kind: str, detail: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "kind": kind,
        "detail": detail,
        "signature": hashlib.sha256(f"{kind}:{canonical}".encode()).hexdigest(),
    }


def _detect_duplicate_persons(db: Session, visible: set[int]) -> list[dict[str, Any]]:
    """conflict/duplicate_person_*：同一空间内同一个人的两份档案（architecture.md §0.9）。

    写入门禁（commands/members.py）只能看见"当下"，而身份重复是涌现属性：档案 A
    建立时没填生日 → 与 B 只构成不可判定的 weak；本人认领后补上生日 → 此刻才与 B
    构成 strong。那一刻没有任何建档请求在跑，只有本审计能发现。

    只报告，不改图（Steward 红线）：每份 User 都带着自己的 Account 与凭据，合并
    需要显式的领域命令与人工确认，不能由后台作业静默决定谁被吞掉。
    """
    if not visible:
        return []
    users = list(db.scalars(select(User).where(User.id.in_(visible), User.deleted_at.is_(None))))
    findings: list[dict[str, Any]] = []
    for pair in person_identity.find_duplicate_pairs(users):
        findings.append(
            _finding(
                "conflict",
                {
                    "code": (
                        "duplicate_person_strong"
                        if pair.strength == person_identity.STRENGTH_STRONG
                        else "duplicate_person_weak"
                    ),
                    "pair": list(pair.user_ids),
                },
            )
        )
    return findings


def _detect_findings(db: Session, space: FamilySpace, visible: set[int]) -> list[dict[str, Any]]:
    """确定性检测：
    - conflict/parent_type_clash：同一 (subject,object) 存在多种 confirmed parent 类事实；
    - conflict/parent_cycle：互为 confirmed parent（A→B 且 B→A）；
    - conflict/duplicate_person_*：同一人两份档案（strong=同名同生日，weak=同名生日缺失）；
    - gap/sibling_missing_parents：direct_sibling 无任何共同 confirmed 父母（不虚构）。
    """
    facts = _applicable_confirmed_facts(db, space, visible)
    findings: list[dict[str, Any]] = _detect_duplicate_persons(db, visible)

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


def _active_member_ids(db: Session, space: FamilySpace) -> set[int]:
    stmt = select(SpaceMember.user_id).where(
        SpaceMember.space_id == space.id, SpaceMember.status == "active"
    )
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
    members = _active_member_ids(db, space)
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
    "classify_execution_error",
    "current_event_watermark",
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
    "scan_due_spaces",
    "schedule_steward_job_for_event",
    "set_kind_cooldown",
    "settle_steward_job",
]
