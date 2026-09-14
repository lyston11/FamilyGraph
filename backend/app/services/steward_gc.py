"""Incremental collection of unreachable staged results.

Discover candidates in a read snapshot, then revalidate the exact bounded set
under the writer. Publications, the latest preview, shared result sources and
undelivered published effects are roots. Never cascade a large child set.
Durable demands, retry budgets and finding receipts are not collection targets.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, delete, exists, func, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, aliased

from app.models.steward import (
    StewardDeliveryIntent,
    StewardGeneration,
    StewardGenerationView,
    StewardInputRevision,
    StewardPublication,
    StewardViewTarget,
)
from app.services import steward_snapshot
from app.services.steward_pipeline import valid_generation, write_transaction
from app.services.steward_snapshot import read_transaction
from app.utils.timeutil import utcnow


@dataclass(frozen=True)
class _Candidate:
    kind: Literal["inactive", "obsolete", "targets", "views", "intents", "generation"]
    generation_id: int | None
    ids: tuple[int, ...]


def _inactive_intents() -> Select[tuple[int]]:
    return (
        select(StewardDeliveryIntent.id)
        .join(StewardGeneration)
        .where(
            StewardGeneration.status.in_(("failed", "superseded")),
            StewardDeliveryIntent.status.in_(("pending", "failed")),
            ~exists(
                select(StewardPublication.space_id).where(
                    StewardPublication.generation_id == StewardGeneration.id,
                )
            ),
        )
    )


def _collectible_generations() -> Select[tuple[int]]:
    referenced = aliased(StewardGenerationView)
    source = aliased(StewardGenerationView)
    newer = aliased(StewardGeneration)
    return select(StewardGeneration.id).where(
        StewardGeneration.status.in_(("published", "failed", "superseded")),
        exists(
            select(newer.id).where(
                newer.space_id == StewardGeneration.space_id,
                newer.id > StewardGeneration.id,
                newer.manifest_sealed.is_(True),
            )
        ),
        ~exists(
            select(StewardPublication.space_id).where(
                StewardPublication.generation_id == StewardGeneration.id,
            )
        ),
        ~exists(
            select(referenced.id)
            .join(source, source.id == referenced.result_view_id)
            .where(
                source.generation_id == StewardGeneration.id,
                referenced.generation_id != StewardGeneration.id,
            )
        ),
        ~exists(
            select(StewardDeliveryIntent.id).where(
                StewardDeliveryIntent.generation_id == StewardGeneration.id,
                StewardGeneration.status == "published",
                StewardDeliveryIntent.status.in_(("pending", "failed")),
            )
        ),
    )


def _replacement(intent: StewardDeliveryIntent) -> Select[tuple[int]]:
    return (
        select(StewardDeliveryIntent.id)
        .join(StewardGeneration)
        .where(
            StewardDeliveryIntent.space_id == intent.space_id,
            StewardDeliveryIntent.effect_fingerprint == intent.effect_fingerprint,
            StewardDeliveryIntent.id > intent.id,
            StewardDeliveryIntent.status.in_(("pending", "failed", "done")),
            StewardGeneration.status == "published",
        )
        .limit(1)
    )


def _obsolete_candidates() -> Select[tuple[int]]:
    successor = aliased(StewardDeliveryIntent)
    successor_generation = aliased(StewardGeneration)
    published = aliased(StewardGeneration)
    global_revision = aliased(StewardInputRevision)
    space_revision = aliased(StewardInputRevision)
    versions = StewardGeneration.input_versions_json
    # Source drift invalidates delivery immediately, even when a failed core
    # successor has not replaced the publication. Compare the same two input
    # rows as valid_generation in this read-only discovery query; inferred-only
    # changes deliberately do not invalidate ordinary confirmed delivery.
    inputs_changed = or_(
        func.coalesce(func.json_extract(versions, "$.version"), "")
        != steward_snapshot.SNAPSHOT_VERSION,
        func.coalesce(func.json_extract(versions, "$.config"), "")
        != steward_snapshot.config_fingerprint(),
        func.coalesce(func.json_extract(versions, "$.global[0]"), -1)
        != func.coalesce(global_revision.structural, 0),
        func.coalesce(func.json_extract(versions, "$.global[1]"), -1)
        != func.coalesce(global_revision.presentation, 0),
        func.coalesce(func.json_extract(versions, "$.space[0]"), -1)
        != func.coalesce(space_revision.structural, 0),
        func.coalesce(func.json_extract(versions, "$.space[1]"), -1)
        != func.coalesce(space_revision.presentation, 0),
    )
    return (
        select(StewardDeliveryIntent.id)
        .join(StewardGeneration)
        .outerjoin(global_revision, global_revision.scope_id == 0)
        .outerjoin(space_revision, space_revision.scope_id == StewardGeneration.space_id)
        .where(
            StewardGeneration.status == "published",
            StewardDeliveryIntent.status.in_(("pending", "failed")),
            (StewardDeliveryIntent.lease_until.is_(None))
            | (StewardDeliveryIntent.lease_until <= utcnow()),
            or_(
                inputs_changed,
                StewardGeneration.manifest_sealed.is_(False),
                StewardGeneration.valid_until.is_(None),
                StewardGeneration.valid_until <= utcnow(),
                exists(
                    select(StewardPublication.space_id)
                    .join(published, published.id == StewardPublication.generation_id)
                    .where(
                        StewardPublication.space_id == StewardDeliveryIntent.space_id,
                        or_(
                            published.fingerprint != StewardGeneration.fingerprint,
                            (StewardDeliveryIntent.kind == "inferred_overlay")
                            & (published.id != StewardGeneration.id),
                        ),
                    )
                ),
                exists(
                    select(successor.id)
                    .join(successor_generation, successor_generation.id == successor.generation_id)
                    .where(
                        successor.space_id == StewardDeliveryIntent.space_id,
                        successor.effect_fingerprint == StewardDeliveryIntent.effect_fingerprint,
                        successor.id > StewardDeliveryIntent.id,
                        successor.status.in_(("pending", "failed", "done")),
                        successor_generation.status == "published",
                    )
                ),
            ),
        )
    )


def _is_obsolete(session: Session, intent_id: int) -> bool:
    intent = session.get(StewardDeliveryIntent, intent_id)
    if (
        intent is None
        or intent.status not in ("pending", "failed")
        or (intent.lease_until is not None and intent.lease_until > utcnow())
    ):
        return False
    generation = session.get(StewardGeneration, intent.generation_id)
    if generation is None or generation.status != "published":
        return False
    if not valid_generation(session, generation):
        return True
    if intent.kind == "inferred_overlay":
        publication = session.get(StewardPublication, intent.space_id)
        if publication is None or publication.generation_id != generation.id:
            return True
    return (
        intent.effect_fingerprint is not None and session.scalar(_replacement(intent)) is not None
    )


def _discover(session: Session, *, cap: int) -> _Candidate | None:
    # A retained failed preview may have a partial intent manifest. Its effects
    # never activated and must terminate while the preview remains available.
    inactive = tuple(
        session.scalars(_inactive_intents().order_by(StewardDeliveryIntent.id).limit(cap))
    )
    if inactive:
        return _Candidate("inactive", None, inactive)
    obsolete = tuple(
        intent_id
        for intent_id in session.scalars(
            _obsolete_candidates().order_by(StewardDeliveryIntent.id).limit(cap)
        )
        if _is_obsolete(session, intent_id)
    )
    if obsolete:
        return _Candidate("obsolete", None, obsolete)
    generation_id = session.scalar(
        _collectible_generations().order_by(StewardGeneration.id).limit(1)
    )
    if generation_id is None:
        return None
    targets = tuple(
        session.scalars(
            select(StewardViewTarget.id)
            .join(StewardGenerationView)
            .where(StewardGenerationView.generation_id == generation_id)
            .limit(cap)
        )
    )
    if targets:
        return _Candidate("targets", generation_id, targets)
    views = tuple(
        session.scalars(
            select(StewardGenerationView.id)
            .where(StewardGenerationView.generation_id == generation_id)
            .limit(cap)
        )
    )
    if views:
        return _Candidate("views", generation_id, views)
    intents = tuple(
        session.scalars(
            select(StewardDeliveryIntent.id)
            .where(StewardDeliveryIntent.generation_id == generation_id)
            .limit(cap)
        )
    )
    if intents:
        return _Candidate("intents", generation_id, intents)
    return _Candidate("generation", generation_id, (generation_id,))


def collect(bind: Engine | Connection, *, batch_size: int = 32) -> int:
    with read_transaction(bind) as read:
        candidate = _discover(read, cap=max(1, min(batch_size, 64)))
    if candidate is None:
        return 0
    with write_transaction(bind) as session:
        if candidate.kind == "inactive":
            eligible = _inactive_intents().where(StewardDeliveryIntent.id.in_(candidate.ids))
            return len(
                session.scalars(
                    update(StewardDeliveryIntent)
                    .where(StewardDeliveryIntent.id.in_(eligible))
                    .values(
                        status="superseded", updated_at=utcnow(), lease_owner=None, lease_until=None
                    )
                    .returning(StewardDeliveryIntent.id)
                ).all()
            )
        if candidate.kind == "obsolete":
            retired = 0
            for intent_id in candidate.ids:
                # Retire only after another published intent owns this same
                # effect, or its exact source is no longer applicable. Never
                # drop the sole live failed effect just to make GC advance.
                if _is_obsolete(session, intent_id):
                    session.execute(
                        update(StewardDeliveryIntent)
                        .where(StewardDeliveryIntent.id == intent_id)
                        .values(
                            status="superseded",
                            updated_at=utcnow(),
                            lease_owner=None,
                            lease_until=None,
                        )
                    )
                    retired += 1
            return retired
        # A publication or a new result reference may have arrived since the
        # read. Check every root again, now by indexed generation primary key.
        if (
            session.scalar(
                _collectible_generations().where(StewardGeneration.id == candidate.generation_id)
            )
            is None
        ):
            return 0
        if candidate.kind == "targets":
            return len(
                session.scalars(
                    delete(StewardViewTarget)
                    .where(
                        StewardViewTarget.id.in_(candidate.ids),
                        StewardViewTarget.view_id.in_(
                            select(StewardGenerationView.id).where(
                                StewardGenerationView.generation_id == candidate.generation_id,
                            )
                        ),
                    )
                    .returning(StewardViewTarget.id)
                ).all()
            )
        if candidate.kind == "views":
            return len(
                session.scalars(
                    delete(StewardGenerationView)
                    .where(
                        StewardGenerationView.id.in_(candidate.ids),
                        StewardGenerationView.generation_id == candidate.generation_id,
                        ~exists(
                            select(StewardViewTarget.id).where(
                                StewardViewTarget.view_id == StewardGenerationView.id,
                            )
                        ),
                    )
                    .returning(StewardGenerationView.id)
                ).all()
            )
        if candidate.kind == "intents":
            return len(
                session.scalars(
                    delete(StewardDeliveryIntent)
                    .where(
                        StewardDeliveryIntent.id.in_(candidate.ids),
                        StewardDeliveryIntent.generation_id == candidate.generation_id,
                    )
                    .returning(StewardDeliveryIntent.id)
                ).all()
            )
        return len(
            session.scalars(
                delete(StewardGeneration)
                .where(
                    StewardGeneration.id == candidate.generation_id,
                    ~exists(
                        select(StewardGenerationView.id).where(
                            StewardGenerationView.generation_id == StewardGeneration.id,
                        )
                    ),
                    ~exists(
                        select(StewardDeliveryIntent.id).where(
                            StewardDeliveryIntent.generation_id == StewardGeneration.id,
                        )
                    ),
                )
                .returning(StewardGeneration.id)
            ).all()
        )
