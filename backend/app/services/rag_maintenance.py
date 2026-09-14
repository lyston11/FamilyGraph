"""Bounded RAG materialization and explicit algorithm changes.

Each batch owns a savepoint inside a real, short SQLite writer. Source rows,
flags and immutable lease evidence are read there; index work, retry ledger
and progress either all survive or all roll back. No network/model work.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.errors import RAG_DISABLED, RAG_SOURCE_NOT_ALLOWED, raise_api_error
from app.models.memory import Memory
from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGDocument, RAGIndexMaintenanceFailure, RAGIndexMaintenanceState
from app.services import memory_rag, memory_sources, platform_features
from app.utils.timeutil import utcnow

MAINTENANCE_POLICY_VERSION = "rag-index-maint-v2"
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 100
MAX_BATCH_SECONDS = 2.0
DEFAULT_LEASE_SECONDS = 120
_FAILURE_BACKOFF_BASE_SECONDS = 60
_STATE_ID = 1


class MaintenanceLeaseLost(Exception):
    """The entire batch must roll back; never turn this into a per-row retry."""


@dataclass(frozen=True)
class MaintenanceLease:
    """An execution's expectations, independent of SQLAlchemy's identity map."""

    attempt: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    policy_version: str
    target_index_version: str
    round: int
    cursor_memory_id: int
    upper_memory_id: int | None
    stage_round: int
    cursor_document_id: int
    upper_document_id: int | None

    @classmethod
    def capture(cls, state: RAGIndexMaintenanceState) -> MaintenanceLease:
        return cls(**{name: getattr(state, name) for name in cls.__dataclass_fields__})


def _state_predicates(lease: MaintenanceLease) -> list[Any]:
    return [
        RAGIndexMaintenanceState.id == _STATE_ID,
        *(
            getattr(RAGIndexMaintenanceState, name) == getattr(lease, name)
            for name in lease.__dataclass_fields__
        ),
    ]


def _ensure_state(db: Session, now: datetime) -> RAGIndexMaintenanceState:
    memory_rag._acquire_index_writer(db)
    db.flush()
    db.execute(
        sqlite_insert(RAGIndexMaintenanceState)
        .values(
            id=_STATE_ID,
            cursor_memory_id=0,
            round=0,
            stage_round=0,
            cursor_document_id=0,
            policy_version=MAINTENANCE_POLICY_VERSION,
            target_index_version=memory_rag.RAG_INDEX_VERSION,
            attempt=0,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    state = db.get(RAGIndexMaintenanceState, _STATE_ID, populate_existing=True)
    assert state is not None
    return state


def _acquire_lease(
    db: Session, state: RAGIndexMaintenanceState, worker_id: str, now: datetime
) -> MaintenanceLease:
    previous = MaintenanceLease.capture(state)
    if (
        not worker_id
        or len(worker_id) > 128
        or previous.policy_version != MAINTENANCE_POLICY_VERSION
        or previous.target_index_version not in memory_rag.INDEX_CHUNKERS
    ):
        raise MaintenanceLeaseLost("unsupported maintenance owner/policy/target")
    lease = replace(
        previous,
        attempt=previous.attempt + 1,
        lease_owner=worker_id,
        lease_expires_at=now + timedelta(seconds=DEFAULT_LEASE_SECONDS),
    )
    changed = db.execute(
        update(RAGIndexMaintenanceState)
        .where(
            *_state_predicates(previous),
            or_(
                RAGIndexMaintenanceState.lease_expires_at.is_(None),
                RAGIndexMaintenanceState.lease_expires_at <= now,
                RAGIndexMaintenanceState.lease_owner == worker_id,
            ),
        )
        .values(
            attempt=lease.attempt,
            lease_owner=lease.lease_owner,
            lease_expires_at=lease.lease_expires_at,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise MaintenanceLeaseLost("maintenance lease changed or belongs to another executor")
    return lease


def _cas_state(
    db: Session, lease: MaintenanceLease, *, now: datetime, values: dict[str, Any]
) -> MaintenanceLease:
    changed = db.execute(
        update(RAGIndexMaintenanceState)
        .where(
            *_state_predicates(lease),
            RAGIndexMaintenanceState.lease_expires_at > now,
            RAGIndexMaintenanceState.policy_version == MAINTENANCE_POLICY_VERSION,
        )
        .values(**values, updated_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise MaintenanceLeaseLost("maintenance lease/round/policy/target fence rejected the batch")
    return replace(
        lease, **{key: value for key, value in values.items() if key in lease.__dataclass_fields__}
    )


def _renew_lease(
    db: Session, lease: MaintenanceLease, *, expected_attempt: int, now: datetime
) -> MaintenanceLease:
    if not isinstance(lease, MaintenanceLease) or lease.attempt != expected_attempt:
        raise MaintenanceLeaseLost("immutable lease evidence is missing or stale")
    return _cas_state(
        db,
        lease,
        now=now,
        values={
            "lease_expires_at": now + timedelta(seconds=DEFAULT_LEASE_SECONDS),
        },
    )


def _start_round(db: Session, lease: MaintenanceLease, *, stage: bool) -> MaintenanceLease:
    upper_name = "upper_document_id" if stage else "upper_memory_id"
    if getattr(lease, upper_name) is not None:
        return lease
    model = RAGDocument if stage else Memory
    upper = int(db.scalar(select(func.max(model.id))) or 0)
    cursor_name = "cursor_document_id" if stage else "cursor_memory_id"
    return _cas_state(db, lease, now=utcnow(), values={upper_name: upper, cursor_name: 0})


def _finish_round(
    db: Session, lease: MaintenanceLease, *, cursor: int, stage: bool
) -> MaintenanceLease:
    model = RAGDocument if stage else Memory
    upper = lease.upper_document_id if stage else lease.upper_memory_id
    assert upper is not None
    completed = (
        db.scalar(select(model.id).where(model.id > cursor, model.id <= upper).limit(1)) is None
    )
    if stage:
        progress = {
            "cursor_document_id": 0 if completed else cursor,
            "upper_document_id": None if completed else upper,
            "stage_round": lease.stage_round + int(completed),
        }
    else:
        progress = {
            "cursor_memory_id": 0 if completed else cursor,
            "upper_memory_id": None if completed else upper,
            "round": lease.round + int(completed),
        }
    lease = _cas_state(db, lease, now=utcnow(), values={**progress, "last_success_at": utcnow()})
    # Refresh for observers only; authorization used the immutable CAS above.
    db.get(RAGIndexMaintenanceState, _STATE_ID, populate_existing=True)
    return lease


def _record_failure(db: Session, memory_id: int, error_code: str, now: datetime) -> None:
    row = db.get(RAGIndexMaintenanceFailure, memory_id, populate_existing=True)
    retry_count = int(row.retry_count) + 1 if row is not None else 1
    retry_at = now + timedelta(seconds=_FAILURE_BACKOFF_BASE_SECONDS * (2 ** min(retry_count, 6)))
    if row is None:
        db.add(
            RAGIndexMaintenanceFailure(
                memory_id=memory_id,
                error_code=error_code,
                retry_count=retry_count,
                next_retry_at=retry_at,
                last_error_at=now,
                updated_at=now,
            )
        )
    else:
        row.retry_count = retry_count
        row.error_code = error_code
        row.next_retry_at = retry_at
        row.last_error_at = row.updated_at = now


def _clear_failure(db: Session, memory_id: int) -> None:
    row = db.get(RAGIndexMaintenanceFailure, memory_id, populate_existing=True)
    if row is not None:
        db.delete(row)


@dataclass(frozen=True)
class _ProjectionWitness:
    document_id: int
    memory_id: int
    revision: int
    index_version: str
    content_sha256: str | None

    @classmethod
    def capture(cls, document: RAGDocument, memory_id: int) -> _ProjectionWitness:
        return cls(
            document.id,
            memory_id,
            document.revision,
            document.index_version,
            document.content_sha256,
        )


def _validate_batch(db: Session, witnesses: list[_ProjectionWitness]) -> None:
    """Recheck all successful sources and effective policy in the writer."""
    memory_rag._require_fresh_rag_enabled(db)
    for witness in witnesses:
        memory = memory_rag._fresh_materializable_memory(db, witness.memory_id)
        document = db.get(RAGDocument, witness.document_id, populate_existing=True)
        if document is None or (
            document.revision != witness.revision
            or document.index_version != witness.index_version
            or document.content_sha256 != witness.content_sha256
        ):
            raise MaintenanceLeaseLost("projection changed before batch completion")
        memory_rag._check_document_metadata(
            document, memory_rag._memory_document_metadata(db, memory)
        )
        if document.content_sha256 != memory_rag.query_hash(memory.content):
            raise MaintenanceLeaseLost("source content changed before batch completion")


def run_maintenance_batch(
    db: Session,
    *,
    worker_id: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """One finite Memory scan; a rejected batch leaves no RAG side effects."""
    memory_rag._acquire_index_writer(db)
    db.flush()
    with db.begin_nested():
        # Refresh even when off: a stale ORM configuration is not policy.
        db.get(PlatformFeatureConfig, 1, populate_existing=True)
        if not platform_features.is_rag_enabled(db):
            return {"skipped": "rag_disabled"}
        lease = _acquire_lease(db, _ensure_state(db, utcnow()), worker_id, utcnow())
        lease = _start_round(db, lease, stage=False)
        size = max(1, min(int(batch_size), MAX_BATCH_SIZE))
        rows = db.scalars(
            select(Memory.id)
            .where(
                Memory.id > lease.cursor_memory_id,
                Memory.id <= lease.upper_memory_id,
            )
            .order_by(Memory.id)
            .limit(size)
        ).all()
        counts = {
            "scanned": 0,
            "materialized": 0,
            "already_current": 0,
            "skipped_invalid": 0,
            "failed": 0,
        }
        witnesses: list[_ProjectionWitness] = []
        cursor = lease.cursor_memory_id
        started = monotonic()
        for memory_id in rows:
            if counts["scanned"] and monotonic() - started >= MAX_BATCH_SECONDS:
                break
            cursor = memory_id
            counts["scanned"] += 1
            failure = db.get(RAGIndexMaintenanceFailure, memory_id, populate_existing=True)
            if failure is not None and failure.next_retry_at > utcnow():
                continue
            memory = db.get(Memory, memory_id, populate_existing=True)
            if memory is None or not memory_sources.memory_materializable(db, memory):
                counts["skipped_invalid"] += 1
                _clear_failure(db, memory_id)
                continue
            try:
                with db.begin_nested():
                    was_complete = memory_rag._memory_projection_complete(db, memory)
                    document = memory_rag.ensure_memory_index(
                        db, memory, target_version=lease.target_index_version
                    )
                    _clear_failure(db, memory_id)
                    witness = _ProjectionWitness.capture(document, memory_id)
                witnesses.append(witness)
                counts["already_current" if was_complete else "materialized"] += 1
            except MaintenanceLeaseLost:
                raise
            except Exception as exc:  # noqa: BLE001 — ordinary bad sources have a bounded retry
                if (
                    isinstance(exc, HTTPException)
                    and isinstance(exc.detail, dict)
                    and (exc.detail.get("__api_error__", {}).get("code") == RAG_DISABLED)
                ):
                    raise
                counts["failed"] += 1
                _record_failure(db, memory_id, "RAG_INDEX_MATERIALIZE_FAILED", utcnow())
        lease = _renew_lease(db, lease, expected_attempt=lease.attempt, now=utcnow())
        _validate_batch(db, witnesses)
        lease = _finish_round(db, lease, cursor=cursor, stage=False)
        db.flush()
        return {**counts, "round": lease.round}


def maintenance_status(db: Session) -> dict[str, Any]:
    """Metadata only; this is not a reader's permission to enumerate sources."""
    state = db.get(RAGIndexMaintenanceState, _STATE_ID, populate_existing=True)
    failures = db.scalar(select(RAGIndexMaintenanceFailure.memory_id).limit(1))
    return {
        "policy_version": state.policy_version if state else MAINTENANCE_POLICY_VERSION,
        "round": int(state.round) if state else 0,
        "cursor_memory_id": int(state.cursor_memory_id) if state else 0,
        "upper_memory_id": state.upper_memory_id if state else None,
        "stage_round": int(state.stage_round) if state else 0,
        "cursor_document_id": int(state.cursor_document_id) if state else 0,
        "upper_document_id": state.upper_document_id if state else None,
        "last_success_at": state.last_success_at.isoformat()
        if state and state.last_success_at
        else None,
        "has_failures": failures is not None,
        "active_index_version": memory_rag.RAG_INDEX_VERSION,
        "target_index_version": state.target_index_version
        if state
        else memory_rag.RAG_INDEX_VERSION,
    }


def stage_index_version(
    db: Session,
    *,
    target_version: str,
    worker_id: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """Explicit, bounded algorithm change, including an authorized rollback.

    Non-Memory sources have no retained full input; never concatenate overlapping
    chunks to guess it. Their existing legitimate active version remains readable.
    """
    if target_version not in memory_rag.INDEX_CHUNKERS:
        raise_api_error(422, RAG_SOURCE_NOT_ALLOWED, "目标索引算法不存在")
    memory_rag._acquire_index_writer(db)
    db.flush()
    with db.begin_nested():
        memory_rag._require_fresh_rag_enabled(db)
        lease = _acquire_lease(db, _ensure_state(db, utcnow()), worker_id, utcnow())
        if lease.target_index_version != target_version:
            lease = _cas_state(
                db,
                lease,
                now=utcnow(),
                values={
                    "target_index_version": target_version,
                    "cursor_document_id": 0,
                    "upper_document_id": None,
                    "stage_round": lease.stage_round + 1,
                },
            )
        lease = _start_round(db, lease, stage=True)
        documents = db.scalars(
            select(RAGDocument)
            .where(
                RAGDocument.id > lease.cursor_document_id,
                RAGDocument.id <= lease.upper_document_id,
            )
            .order_by(RAGDocument.id)
            .limit(max(1, min(int(batch_size), MAX_BATCH_SIZE)))
            .execution_options(populate_existing=True)
        ).all()
        staged = flipped = skipped = scanned = 0
        cursor = lease.cursor_document_id
        witnesses: list[_ProjectionWitness] = []
        started = monotonic()
        for document in documents:
            if scanned and monotonic() - started >= MAX_BATCH_SECONDS:
                break
            scanned += 1
            cursor = document.id
            if (
                document.status != "active"
                or document.index_version == target_version
                or document.source_type != "memory"
                or not document.source_id.isdigit()
                or not memory_rag._document_materializable(db, document)
            ):
                skipped += 1
                continue
            memory = memory_rag._fresh_materializable_memory(db, int(document.source_id))
            memory_rag._check_document_metadata(
                document, memory_rag._memory_document_metadata(db, memory)
            )
            old_version = document.index_version
            memory_rag._materialize_chunks(
                db, document, memory.content, memory.revision, target_version=target_version
            )
            db.flush()
            memory_rag._fresh_materializable_memory(db, memory.id)
            memory_rag._require_fresh_rag_enabled(db)
            changed = db.execute(
                update(RAGDocument)
                .where(
                    RAGDocument.id == document.id,
                    RAGDocument.index_version == old_version,
                    RAGDocument.revision == memory.revision,
                    RAGDocument.source_revision == memory.revision,
                    RAGDocument.status == "active",
                    RAGDocument.invalidation_reason.is_(None),
                    RAGDocument.content_sha256 == memory_rag.query_hash(memory.content),
                    select(RAGIndexMaintenanceState.id)
                    .where(
                        *_state_predicates(lease),
                        RAGIndexMaintenanceState.lease_expires_at > utcnow(),
                    )
                    .exists(),
                )
                .values(index_version=target_version, updated_at=utcnow())
                .execution_options(synchronize_session=False)
            ).rowcount
            if changed != 1:
                raise MaintenanceLeaseLost("source or target fence rejected the version switch")
            db.refresh(document)
            witnesses.append(_ProjectionWitness.capture(document, memory.id))
            staged += 1
            flipped += 1
        lease = _renew_lease(db, lease, expected_attempt=lease.attempt, now=utcnow())
        _validate_batch(db, witnesses)
        _finish_round(db, lease, cursor=cursor, stage=True)
        db.flush()
        return {"scanned": scanned, "staged": staged, "flipped": flipped, "skipped": skipped}


__all__ = [
    "MAINTENANCE_POLICY_VERSION",
    "MaintenanceLease",
    "MaintenanceLeaseLost",
    "maintenance_status",
    "run_maintenance_batch",
    "stage_index_version",
]
