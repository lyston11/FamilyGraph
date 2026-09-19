"""Authenticated, durable viewer requests independent of the event cursor."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app import config
from app.db import SessionLocal
from app.errors import raise_api_error
from app.models.account import Account
from app.models.steward import (
    StewardGeneration,
    StewardGenerationView,
    StewardJob,
    StewardPublication,
    StewardRetryBudget,
    StewardViewDemand,
    StewardViewTarget,
)
from app.services import steward_runtime, steward_snapshot
from app.services.relationship_graph import load_graph
from app.services.relationship_resolver import reachable_targets
from app.services.steward_pipeline import search_fingerprint, valid_generation, write_transaction
from app.services.steward_views import authorize
from app.utils.timeutil import utcnow


def _already_covered(session: Session, *, account: Account, space_id: int) -> bool:
    """Coalesce only work covered in the caller's authorized read snapshot."""
    from app.services.steward import current_event_watermark

    now = utcnow()
    query = (
        select(StewardGeneration)
        .options(
            load_only(
                StewardGeneration.space_id,
                StewardGeneration.manifest_sealed,
                StewardGeneration.valid_until,
                StewardGeneration.input_versions_json,
                raiseload=True,
            )
        )
        .join(
            StewardGenerationView,
            StewardGenerationView.generation_id == StewardGeneration.id,
        )
        .where(
            StewardGeneration.space_id == space_id,
            StewardGeneration.manifest_sealed.is_(True),
            StewardGeneration.valid_until > now,
            StewardGenerationView.space_id == space_id,
            StewardGenerationView.viewer_account_id == account.id,
            StewardGenerationView.root_user_id == account.user_id,
        )
    )
    published = session.scalar(
        query.join(
            StewardPublication, StewardPublication.generation_id == StewardGeneration.id
        ).where(
            StewardPublication.space_id == space_id,
            StewardGeneration.status == "published",
            StewardGenerationView.status == "ready",
        )
    )
    # Preserve the existing satisfied-view contract: unrelated global events,
    # including publication's completion event, do not create a new demand.
    if published is not None and valid_generation(session, published, now=now):
        return True
    running = session.scalar(
        query.join(StewardJob, StewardJob.id == StewardGeneration.job_id)
        .join(
            StewardViewDemand,
            (StewardViewDemand.space_id == StewardGeneration.space_id)
            & (StewardViewDemand.viewer_account_id == StewardGenerationView.viewer_account_id),
        )
        .where(
            StewardGeneration.status == "running",
            StewardGenerationView.status.in_(("pending", "ready")),
            StewardViewDemand.fulfilled_revision < StewardViewDemand.revision,
            StewardGenerationView.demand_revision >= StewardViewDemand.revision,
            StewardJob.space_id == space_id,
            StewardJob.status.in_(("leased", "running")),
            StewardJob.leased_by == StewardGeneration.lease_owner,
            StewardJob.attempt == StewardGeneration.lease_attempt,
            StewardJob.lease_expires_at > now,
            StewardJob.trigger_cursor >= current_event_watermark(session),
        )
        .order_by(StewardGeneration.id.desc())
        .limit(1)
    )
    return running is not None and valid_generation(session, running, now=now)


def register(
    *, account: Account, space_id: int, focus_user_id: int | None = None, retry: bool = False
) -> str:
    from app.services.steward import _enqueue_core_job_locked, current_event_watermark

    if not config.STEWARD_ENABLED:
        return "queued"
    with SessionLocal() as scope:
        bind = scope.get_bind()
    for _ in range(3):
        with steward_snapshot.read_transaction(bind) as session:
            current = authorize(session, account=account, space_id=space_id)
            versions = steward_snapshot.input_versions(session, space_id)
            if (
                focus_user_id is None
                and not retry
                and _already_covered(session, account=current, space_id=space_id)
            ):
                return "already_active"
            if focus_user_id is not None:
                graph = load_graph(session, viewer_user_id=current.user_id, space_id=space_id)
                # 09-19：邻接表含路径中间人，「可达」不再等于「授权目标」——
                # 重点关注目标必须是本空间的授权节点。
                reached = reachable_targets(graph)
                if focus_user_id not in reached or focus_user_id not in graph.node_genders:
                    raise_api_error(422, "VALIDATION_ERROR", "重点关注目标不在当前授权骨架内")
        with write_transaction(bind) as session:
            authorize(session, account=account, space_id=space_id)
            if not steward_snapshot.versions_match(session, space_id=space_id, expected=versions):
                continue
            now = utcnow()
            publication = session.get(StewardPublication, space_id)
            published = (
                session.get(StewardGeneration, publication.generation_id)
                if publication is not None
                else None
            )
            published_view = (
                session.scalar(
                    select(StewardGenerationView).where(
                        StewardGenerationView.generation_id == publication.generation_id,
                        StewardGenerationView.viewer_account_id == account.id,
                    )
                )
                if publication is not None
                else None
            )
            satisfied = (
                published is not None
                and published_view is not None
                and published_view.status == "ready"
                and valid_generation(session, published)
            )
            demand = session.scalar(
                select(StewardViewDemand).where(
                    StewardViewDemand.space_id == space_id,
                    StewardViewDemand.viewer_account_id == account.id,
                )
            )
            if satisfied and not retry:
                return "already_active"
            latest = session.scalar(
                select(StewardGeneration)
                .where(
                    StewardGeneration.space_id == space_id,
                    StewardGeneration.manifest_sealed.is_(True),
                )
                .order_by(StewardGeneration.id.desc())
                .limit(1)
            )
            failed_same_input = (
                latest is not None
                and latest.status == "failed"
                and valid_generation(session, latest)
            )
            if demand is None:
                demand = StewardViewDemand(
                    space_id=space_id,
                    viewer_account_id=account.id,
                    revision=1,
                    fulfilled_revision=0,
                    focus_user_id=focus_user_id,
                    requested_at=now,
                )
                session.add(demand)
            else:
                previously_fulfilled = demand.fulfilled_revision >= demand.revision or (
                    published_view is not None and published_view.demand_revision >= demand.revision
                )
                if previously_fulfilled and not satisfied:
                    demand.revision += 1
                if focus_user_id is not None:
                    demand.focus_user_id = focus_user_id
                demand.requested_at = now
            retry_granted = False
            if retry and (
                demand.retry_requested_at is None
                or demand.retry_requested_at
                + timedelta(seconds=config.STEWARD_RERUN_COOLDOWN_SECONDS)
                <= now
            ):
                retry_granted = True
                demand.retry_requested_at = now
                demand.revision += 1
                if latest is not None:
                    view = session.scalar(
                        select(StewardGenerationView).where(
                            StewardGenerationView.generation_id == latest.id,
                            StewardGenerationView.viewer_account_id == account.id,
                        )
                    )
                    if view is not None and view.structural_hash:
                        query = select(StewardViewTarget.target_user_id).where(
                            StewardViewTarget.view_id == view.id,
                            StewardViewTarget.status == "failed",
                        )
                        if focus_user_id is not None:
                            query = query.where(StewardViewTarget.target_user_id == focus_user_id)
                        for target_id in session.scalars(query).all():
                            budget = session.scalar(
                                select(StewardRetryBudget).where(
                                    StewardRetryBudget.space_id == space_id,
                                    StewardRetryBudget.fingerprint
                                    == search_fingerprint(view.structural_hash),
                                    StewardRetryBudget.scope == f"search:{account.id}:{target_id}",
                                )
                            )
                            if budget is not None:
                                if budget.attempts >= budget.max_attempts:
                                    budget.max_attempts = budget.attempts + 1
                                budget.exhausted, budget.retry_after = False, None
                                budget.manual_grants += 1
                                budget.manual_retry_at, budget.updated_at = now, now
            if failed_same_input and not retry_granted:
                return "already_active"  # GETs/duplicate clicks never reset a failed budget.
            active = session.scalar(
                select(StewardJob).where(
                    StewardJob.space_id == space_id,
                    StewardJob.status.in_(("queued", "leased", "running")),
                )
            )
            created = active is None
            if active is None:
                _enqueue_core_job_locked(
                    session,
                    space_id=space_id,
                    cause="integrity_scan",
                    trigger_cursor=current_event_watermark(session),
                    now=now,
                    respect_succeeded_shortcircuit=False,
                )
            else:
                active.trigger_cursor = max(active.trigger_cursor, current_event_watermark(session))
            session.flush()
        # Wake the existing bounded worker immediately, rather than waiting for
        # a possibly five-second maintenance tick before first skeleton paint.
        steward_runtime.launch_due(space_id=space_id, limit=1)
        return "queued" if created else "already_active"
    raise_api_error(409, "PFV_INPUT_CHANGED", "家谱正在更新，请稍后重试")
