"""Bounded RAG index maintenance (workstream D).

Responsibilities, deliberately independent of Steward jobs, behavior
projections and assist switches:

- A bounded full-round backfill over monotonic Memory IDs: legal confirmed
  memories created while RAG was off get materialized after RAG becomes
  effectively enabled (D-AC1).  A completed round restarts from ID 0 so later
  rounds re-check low IDs whose sources became legal afterwards.
- A persistent lease fence (owner + expires + attempt): a stale executor
  cannot advance the cursor or flip the active index version (D-AC5).
- A retry ledger with backoff per memory: one bad record never blocks others
  and never retries unboundedly (D-R3).  Rows hold IDs and stable error codes
  only — no text, no person names (D-R6).

Cursor advance, failure registration and materialization share one short
transaction per batch; the effective RAG switch (deployment AND platform DB)
is re-evaluated inside every batch before any write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import RAG_SOURCE_NOT_ALLOWED, raise_api_error
from app.models.memory import Memory
from app.models.rag import (
    RAGChunk,
    RAGDocument,
    RAGIndexMaintenanceFailure,
    RAGIndexMaintenanceState,
)
from app.services import memory_rag, memory_sources, platform_features
from app.utils.timeutil import utcnow

MAINTENANCE_POLICY_VERSION = "rag-index-maint-v1"
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 500
DEFAULT_LEASE_SECONDS = 120
_FAILURE_BACKOFF_BASE_SECONDS = 60
_MAX_FAILURE_RETRIES = 5
_STATE_ID = 1


class MaintenanceLeaseLost(Exception):
    """Raised when the lease fence rejects a cursor/state write."""


@dataclass(frozen=True)
class MaintenanceBatchResult:
    scanned: int
    materialized: int
    already_current: int
    skipped_invalid: int
    failed: int
    round_completed: bool
    cursor_memory_id: int
    round: int

    def safe_counts(self) -> dict[str, int]:
        """Observability projection: counts only, no text (D-R6)."""
        return {
            "scanned": self.scanned,
            "materialized": self.materialized,
            "already_current": self.already_current,
            "skipped_invalid": self.skipped_invalid,
            "failed": self.failed,
            "round": self.round,
        }


def _ensure_state(db: Session, now: datetime) -> RAGIndexMaintenanceState:
    state = db.get(RAGIndexMaintenanceState, _STATE_ID)
    if state is None:
        state = RAGIndexMaintenanceState(
            id=_STATE_ID,
            cursor_memory_id=0,
            round=0,
            policy_version=MAINTENANCE_POLICY_VERSION,
            attempt=0,
            updated_at=now,
        )
        db.add(state)
        db.flush()
    return state


def _acquire_lease(
    db: Session, state: RAGIndexMaintenanceState, worker_id: str, now: datetime
) -> int:
    """Conditional fence: expired/absent lease or the same owner may proceed."""
    claimable = state.lease_expires_at is None or state.lease_expires_at <= now
    if not claimable and state.lease_owner != worker_id:
        raise MaintenanceLeaseLost("another executor holds the maintenance lease")
    state.lease_owner = worker_id
    state.lease_expires_at = now + timedelta(seconds=DEFAULT_LEASE_SECONDS)
    state.attempt = int(state.attempt) + 1
    state.updated_at = now
    db.flush()
    return int(state.attempt)


def _renew_lease(
    db: Session, state: RAGIndexMaintenanceState, *, expected_attempt: int, now: datetime
) -> None:
    """Fenced write: the cursor can only move forward from the leased attempt."""
    current = db.get(RAGIndexMaintenanceState, _STATE_ID)
    if current is None or int(current.attempt) != expected_attempt:
        raise MaintenanceLeaseLost("lease attempt changed; refusing to advance cursor")
    current.lease_expires_at = now + timedelta(seconds=DEFAULT_LEASE_SECONDS)
    current.updated_at = now


def _failure_backoff(retry_count: int) -> datetime:
    return utcnow() + timedelta(seconds=_FAILURE_BACKOFF_BASE_SECONDS * (2 ** min(retry_count, 6)))


def _record_failure(db: Session, memory_id: int, error_code: str, now: datetime) -> None:
    row = db.get(RAGIndexMaintenanceFailure, memory_id)
    if row is None:
        db.add(
            RAGIndexMaintenanceFailure(
                memory_id=memory_id,
                error_code=error_code,
                retry_count=1,
                next_retry_at=_failure_backoff(1),
                last_error_at=now,
                updated_at=now,
            )
        )
    else:
        row.retry_count = int(row.retry_count) + 1
        row.error_code = error_code
        row.next_retry_at = _failure_backoff(int(row.retry_count))
        row.last_error_at = now
        row.updated_at = now


def _clear_failure(db: Session, memory_id: int) -> None:
    row = db.get(RAGIndexMaintenanceFailure, memory_id)
    if row is not None:
        db.delete(row)


def run_maintenance_batch(
    db: Session,
    *,
    worker_id: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """One bounded maintenance batch; safe for repeated invocation.

    The effective RAG switch (deployment AND platform DB state) is evaluated
    inside the batch: off → no new work; a batch already in progress still
    re-checks before committing materialization (D-R4).
    """
    size = max(1, min(int(batch_size), MAX_BATCH_SIZE))
    now = utcnow()
    if not platform_features.is_rag_enabled(db):
        return {"skipped": "rag_disabled"}
    state = _ensure_state(db, now)
    attempt = _acquire_lease(db, state, worker_id, now)

    rows = db.scalars(
        select(Memory)
        .where(Memory.id > int(state.cursor_memory_id))
        .order_by(Memory.id.asc())
        .limit(size)
    ).all()
    scanned = materialized = already_current = skipped_invalid = failed = 0
    for memory in rows:
        scanned += 1
        failure = db.get(RAGIndexMaintenanceFailure, memory.id)
        if failure is not None and failure.next_retry_at > now:
            continue  # backoff window: leave for a later batch, never busy-loop
        if not memory_sources.memory_materializable(db, memory):
            skipped_invalid += 1
            _clear_failure(db, memory.id)
            continue
        try:
            # Savepoint isolation: a poisoned record rolls back only itself.
            with db.begin_nested():
                had_projection = (
                    db.scalar(
                        select(RAGDocument.id).where(
                            RAGDocument.source_type == "memory",
                            RAGDocument.source_id == str(memory.id),
                            RAGDocument.revision == memory.revision,
                            RAGDocument.status == "active",
                            RAGDocument.index_version == memory_rag.RAG_INDEX_VERSION,
                        )
                    )
                    is not None
                )
                memory_rag.ensure_memory_index(db, memory)
                # Re-validate inside the same transaction: a concurrent
                # revocation that already committed must not leave a fresh
                # projection behind.
                if not memory_sources.memory_materializable(db, memory):
                    raise_api_error(409, RAG_SOURCE_NOT_ALLOWED, "来源已在处理中失效")
                _clear_failure(db, memory.id)
                if had_projection:
                    already_current += 1
                else:
                    materialized += 1
        except Exception:  # noqa: BLE001 — one bad record must not block the batch
            failed += 1
            _record_failure(db, memory.id, "RAG_INDEX_MATERIALIZE_FAILED", now)

    round_completed = scanned < size
    cursor = int(rows[-1].id) if rows else int(state.cursor_memory_id)
    if round_completed:
        cursor = 0
    _renew_lease(db, state, expected_attempt=attempt, now=utcnow())
    fresh_state = db.get(RAGIndexMaintenanceState, _STATE_ID)
    assert fresh_state is not None
    # Forward-only within a round; a completed round resets to cover later-
    # legalizing low IDs.  A stale executor's write is rejected by the fence.
    if round_completed:
        fresh_state.round = int(fresh_state.round) + 1
    fresh_state.cursor_memory_id = cursor
    fresh_state.last_success_at = utcnow()
    fresh_state.updated_at = utcnow()
    db.flush()
    result = MaintenanceBatchResult(
        scanned=scanned,
        materialized=materialized,
        already_current=already_current,
        skipped_invalid=skipped_invalid,
        failed=failed,
        round_completed=round_completed,
        cursor_memory_id=fresh_state.cursor_memory_id,
        round=int(fresh_state.round),
    )
    return result.safe_counts()


def maintenance_status(db: Session) -> dict[str, Any]:
    """Safe observability metadata: cursor/round/counts, never content."""
    state = db.get(RAGIndexMaintenanceState, _STATE_ID)
    failures = db.scalar(select(RAGIndexMaintenanceFailure.memory_id).limit(1))
    return {
        "policy_version": state.policy_version if state else MAINTENANCE_POLICY_VERSION,
        "round": int(state.round) if state else 0,
        "cursor_memory_id": int(state.cursor_memory_id) if state else 0,
        "last_success_at": state.last_success_at.isoformat()
        if state and state.last_success_at
        else None,
        "has_failures": failures is not None,
        "active_index_version": memory_rag.RAG_INDEX_VERSION,
    }


def stage_index_version(
    db: Session,
    *,
    target_version: str,
    worker_id: str,
) -> dict[str, int]:
    """Materialize every active legal document's chunks at ``target_version``
    and atomically flip the document activity pointer.

    Old-version chunks are retained (exact historical reads keep working);
    the flip is a conditional update guarded by the current version so a
    stale executor cannot move a document backwards (D-AC6).
    """
    if target_version == memory_rag.RAG_INDEX_VERSION:
        raise_api_error(422, RAG_SOURCE_NOT_ALLOWED, "目标版本与当前版本相同")
    now = utcnow()
    documents = db.scalars(
        select(RAGDocument).where(
            RAGDocument.status == "active",
            RAGDocument.index_version != target_version,
        )
    ).all()
    staged = flipped = skipped = 0
    for document in documents:
        memory: Memory | None = None
        if document.source_type == "memory":
            memory = db.get(Memory, int(document.source_id))
            if memory is None or not memory_sources.memory_materializable(db, memory):
                skipped += 1
                continue
            pieces = memory_rag._chunk_text(memory.content)
        else:
            pieces = memory_rag._chunk_text(
                "".join(
                    chunk.text
                    for chunk in db.scalars(
                        select(RAGChunk).where(
                            RAGChunk.document_id == document.id,
                            RAGChunk.index_version == document.index_version,
                            RAGChunk.status == "active",
                        )
                    ).all()
                )
            )
        existing = {
            int(chunk.chunk_index)
            for chunk in db.scalars(
                select(RAGChunk).where(
                    RAGChunk.document_id == document.id,
                    RAGChunk.index_version == target_version,
                )
            ).all()
        }
        for index, text_value in enumerate(pieces):
            if index in existing:
                continue
            db.add(
                RAGChunk(
                    document_id=document.id,
                    chunk_index=index,
                    source_revision=document.revision,
                    text=text_value,
                    token_estimate=memory_rag._estimate_tokens(text_value),
                    index_version=target_version,
                    status="active",
                    created_at=now,
                )
            )
        staged += 1
        flipped += int(
            db.execute(
                update(RAGDocument)
                .where(
                    RAGDocument.id == document.id,
                    RAGDocument.index_version == document.index_version,
                )
                .values(index_version=target_version, updated_at=now)
            ).rowcount
            or 0
        )
    db.flush()
    del worker_id
    return {"staged": staged, "flipped": flipped, "skipped": skipped}


__all__ = [
    "MAINTENANCE_POLICY_VERSION",
    "MaintenanceLeaseLost",
    "maintenance_status",
    "run_maintenance_batch",
    "stage_index_version",
]
