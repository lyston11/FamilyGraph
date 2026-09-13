"""Steward generation 进度/指纹/预算层（09-13 design §3/§5.2 MVP 合同）。

职责边界：
- generation 行承担「发布代次 + 执行游标 + 空间输入指纹」的持久记录；
- per-viewer 进度行（StewardGenerationView）承担 R4 的真实进度来源——
  完成数来自已保存并验证的视图重建结果，不是耗时百分比；
- retry budget 按 (space, fingerprint, scope) 跨代持久：换 job/重启/定时扫描
  不清零；相关输入变化（新指纹）即新预算；admin_rerun 授予一次有界机会。

所有写入都在调用方给定的短事务边界内完成（本模块不开事务、不 commit）；
fingerprint 比较是「无变化图重算短路」（R5）的唯一判据，与到期检查/恢复
分开——扫描与 demand 不因指纹命中跳过卡片到期检查与租约恢复。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.models.personal_family_view import PersonalFamilyBridge
from app.models.relationship_facts import SourceFact
from app.models.space import SpaceMember, SpaceProfileRef
from app.models.steward import StewardGeneration, StewardGenerationView, StewardRetryBudget
from app.utils.timeutil import utcnow

logger = logging.getLogger(__name__)

#: 视图重建阶段的预算 scope（当前唯一必需工作阶段）
SCOPE_PFV = "pfv"


class StewardRetryBudgetExhausted(RuntimeError):
    """必需阶段重试预算耗尽：整代失败，不发布、不推进水位。"""


def space_input_fingerprint(session: Session, *, space_id: int) -> str:
    """空间输入指纹：confirmed 事实 + 有效桥接 + 成员/引用节点集合。

    参与派生缓存与个人视图重建的全部结构性输入都进入指纹（design §4.2 的
    结构/授权层 MVP 子集：词典/展示输入由 PFV input_hash 另行守护，此处
    只负责「跳过结构重算」的短路判据——词典变化不影响结构结果）。
    """
    digest = hashlib.sha256()
    fact_rows = session.execute(
        select(SourceFact.id, SourceFact.revision, SourceFact.fact_type).where(
            SourceFact.state == "confirmed",
            (SourceFact.space_id == space_id) | (SourceFact.space_id.is_(None)),
        )
    ).all()
    for fact_id, revision, fact_type in sorted(fact_rows):
        digest.update(f"f:{fact_id}:{revision}:{fact_type}\n".encode())
    bridge_rows = session.execute(
        select(
            PersonalFamilyBridge.id,
            PersonalFamilyBridge.revision,
            PersonalFamilyBridge.status,
            PersonalFamilyBridge.expires_at,
        ).where(
            (PersonalFamilyBridge.lineage_space_a_id == space_id)
            | (PersonalFamilyBridge.lineage_space_b_id == space_id),
        )
    ).all()
    moment = utcnow()
    for bridge_id, revision, status, expires_at in sorted(bridge_rows):
        expired = status != "active" or (expires_at is not None and expires_at <= moment)
        digest.update(f"b:{bridge_id}:{revision}:{0 if expired else 1}\n".encode())
    member_rows = session.scalars(
        select(SpaceMember.user_id).where(
            SpaceMember.space_id == space_id, SpaceMember.status == "active"
        )
    ).all()
    for user_id in sorted(member_rows):
        digest.update(f"m:{user_id}\n".encode())
    ref_rows = session.scalars(
        select(SpaceProfileRef.user_id).where(
            SpaceProfileRef.space_id == space_id, SpaceProfileRef.status == "active"
        )
    ).all()
    for user_id in sorted(ref_rows):
        digest.update(f"r:{user_id}\n".encode())
    return digest.hexdigest()


def create_running_generation(
    session: Session,
    *,
    space_id: int,
    job_id: int | None,
    execution_cursor: int,
    fingerprint: str | None,
    now: datetime,
) -> StewardGeneration:
    """登记新代次；同空间残留 running 代次原子置 superseded（无活跃作业归属）。"""
    stale = list(
        session.scalars(
            select(StewardGeneration).where(
                StewardGeneration.space_id == space_id,
                StewardGeneration.status == "running",
            )
        )
    )
    for row in stale:
        row.status = "superseded"
        row.updated_at = now
    generation = StewardGeneration(
        space_id=space_id,
        job_id=job_id,
        status="running",
        execution_cursor=execution_cursor,
        fingerprint=fingerprint,
        stats_json={},
        created_at=now,
        updated_at=now,
    )
    session.add(generation)
    session.flush()
    return generation


def unchanged_since_published(session: Session, *, space_id: int, fingerprint: str) -> bool:
    """无变化短路判据（R5）：最近 published 代次指纹一致且无失败/未完成视图。

    有 failed 视图的代次不算「完整发布」——其视图仍是待重建的必需工作，
    下一次作业不得凭指纹跳过（否则失败视图将永远得不到重算）。
    """
    generation = session.scalar(
        select(StewardGeneration)
        .where(
            StewardGeneration.space_id == space_id,
            StewardGeneration.status == "published",
            StewardGeneration.fingerprint == fingerprint,
        )
        .order_by(StewardGeneration.id.desc())
        .limit(1)
    )
    if generation is None:
        return False
    counts = generation_progress_counts(session, generation_id=generation.id)
    return counts["failed"] == 0 and counts["pending"] == 0


def latest_published_fingerprint(session: Session, *, space_id: int) -> str | None:
    """最近一次 published 代次的指纹（无变化短路判据；无则 None）。"""
    return session.scalar(
        select(StewardGeneration.fingerprint)
        .where(
            StewardGeneration.space_id == space_id,
            StewardGeneration.status == "published",
            StewardGeneration.fingerprint.is_not(None),
        )
        .order_by(StewardGeneration.id.desc())
        .limit(1)
    )


def mark_published(session: Session, *, generation_id: int, stats: dict[str, Any]) -> None:
    """发布事务内调用：代次置 published（与作业 settle 同一短事务）。"""
    row = session.get(StewardGeneration, generation_id)
    if row is None:  # pragma: no cover - 开始阶段必然已创建
        return
    now = utcnow()
    row.status = "published"
    row.stats_json = dict(stats)
    row.published_at = now
    row.updated_at = now
    session.flush()


def mark_failed(session: Session, *, space_id: int, error_code: str) -> None:
    """失败收敛：本空间残留 running 代次置 failed（结算失败/预算耗尽路径）。"""
    now = utcnow()
    rows = list(
        session.scalars(
            select(StewardGeneration).where(
                StewardGeneration.space_id == space_id,
                StewardGeneration.status == "running",
            )
        )
    )
    for row in rows:
        row.status = "failed"
        row.error_code = error_code
        row.updated_at = now
    if rows:
        session.flush()


def record_view_progress(
    session: Session,
    *,
    generation_id: int,
    space_id: int,
    viewer_account_id: int,
    root_user_id: int,
    status: str,
    completed_count: int = 0,
    total_count: int = 0,
    revision: int = 0,
    failed_reason: str | None = None,
) -> None:
    """单视图重建结束（同一短事务内）登记真实进度行（upsert 幂等）。"""
    now = utcnow()
    row = session.scalar(
        select(StewardGenerationView).where(
            StewardGenerationView.generation_id == generation_id,
            StewardGenerationView.viewer_account_id == viewer_account_id,
        )
    )
    if row is None:
        row = StewardGenerationView(
            generation_id=generation_id,
            space_id=space_id,
            viewer_account_id=viewer_account_id,
            root_user_id=root_user_id,
            status=status,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    row.status = status
    row.completed_count = completed_count
    row.total_count = total_count
    row.revision = revision
    row.failed_reason = failed_reason
    row.updated_at = now
    session.flush()


# ---- 跨代重试预算（design §5.2）----


def budget_state(
    session: Session, *, space_id: int, fingerprint: str, scope: str
) -> StewardRetryBudget | None:
    return session.scalar(
        select(StewardRetryBudget).where(
            StewardRetryBudget.space_id == space_id,
            StewardRetryBudget.fingerprint == fingerprint,
            StewardRetryBudget.scope == scope,
        )
    )


def budget_exhausted(session: Session, *, space_id: int, fingerprint: str, scope: str) -> bool:
    row = budget_state(session, space_id=space_id, fingerprint=fingerprint, scope=scope)
    if row is None:
        return False
    if not row.exhausted and row.attempts >= row.max_attempts:
        row.exhausted = True
        row.updated_at = utcnow()
        session.flush()
    return bool(row.exhausted)


def record_stage_failure(session: Session, *, space_id: int, fingerprint: str, scope: str) -> bool:
    """登记一次阶段失败；预算耗尽时置 exhausted 并返回 True（整代失败）。"""
    now = utcnow()
    row = budget_state(session, space_id=space_id, fingerprint=fingerprint, scope=scope)
    if row is None:
        row = StewardRetryBudget(
            space_id=space_id,
            fingerprint=fingerprint,
            scope=scope,
            attempts=1,
            max_attempts=config.STEWARD_STAGE_MAX_ATTEMPTS,
            exhausted=False,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return False
    if row.exhausted:
        return True
    row.attempts += 1
    if row.attempts >= row.max_attempts:
        row.exhausted = True
    row.updated_at = now
    session.flush()
    return bool(row.exhausted)


def grant_manual_retry(session: Session, *, space_id: int, fingerprint: str, scope: str) -> bool:
    """人工重跑的一次有界机会：仅当已耗尽且尚未被放宽过时 +1 上限（审计留在行上）。

    返回是否放宽。重复人工重试合并——已放宽的耗尽行不再重复放宽。
    """
    row = budget_state(session, space_id=space_id, fingerprint=fingerprint, scope=scope)
    if row is None or not row.exhausted:
        return False
    row.max_attempts += 1
    row.exhausted = False
    row.updated_at = utcnow()
    session.flush()
    return True


def view_progress_for_viewer(
    session: Session, *, space_id: int, viewer_account_id: int
) -> StewardGenerationView | None:
    """读取该查看者在最近非 superseded 代次的进度行（渐进 API 进度来源）。"""
    return session.scalar(
        select(StewardGenerationView)
        .join(StewardGeneration, StewardGenerationView.generation_id == StewardGeneration.id)
        .where(
            StewardGenerationView.space_id == space_id,
            StewardGenerationView.viewer_account_id == viewer_account_id,
            StewardGeneration.status.in_(("running", "published", "failed")),
        )
        .order_by(StewardGenerationView.generation_id.desc())
        .limit(1)
    )


def generation_progress_counts(session: Session, *, generation_id: int) -> dict[str, int]:
    """代次内 ready/failed/pending 视图计数（admin 端进度聚合用）。"""
    rows = session.execute(
        select(StewardGenerationView.status, func.count(StewardGenerationView.id))
        .where(StewardGenerationView.generation_id == generation_id)
        .group_by(StewardGenerationView.status)
    ).all()
    counts: dict[str, int] = {"ready": 0, "failed": 0, "pending": 0}
    for status, count in rows:
        counts[str(status)] = int(count)
    return counts


__all__ = [
    "SCOPE_PFV",
    "StewardRetryBudgetExhausted",
    "budget_exhausted",
    "budget_state",
    "create_running_generation",
    "generation_progress_counts",
    "grant_manual_retry",
    "latest_published_fingerprint",
    "mark_failed",
    "mark_published",
    "record_stage_failure",
    "record_view_progress",
    "space_input_fingerprint",
    "view_progress_for_viewer",
]
