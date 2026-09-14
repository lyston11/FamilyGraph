"""One reader projection for server-authenticated, exact RAG citations."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.agent import AgentMessage, AgentRun, AgentSession
from app.services import memory_sources

# Sidecars may submit text/web fields only. These fields belong to the server,
# including when an old event predates the authenticated citation contract.
SERVER_FIELDS = frozenset(
    {
        "citations",
        "unavailable_citation_count",
        "omitted_citation_count",
        "citations_complete",
        "context_reference",
        "context_reference_json",
        "context_build_id",
        "provenance",
        "_source_ref",
        "source_ref",
        "content_hash",
        "document_id",
        "chunk_id",
        "index_version",
    }
)
MAX_PAYLOAD_BYTES = 16 * 1024


def public_base(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in SERVER_FIELDS}


def event_message(db: Session, run: AgentRun, seq: int) -> AgentMessage | None:
    return db.scalar(
        select(AgentMessage).where(
            AgentMessage.session_id == run.session_id,
            AgentMessage.role == "assistant",
            AgentMessage.idempotency_key == f"run:{run.id}:event:{seq}",
        )
    )


def project_message_citations(
    db: Session,
    message: AgentMessage,
    account: Account,
) -> tuple[list[dict[str, Any]], int]:
    if message.role != "assistant":
        return [], 0
    stored = message.content_json.get("citations", [])
    if not isinstance(stored, list):
        stored = []
    unavailable = message.content_json.get("unavailable_citation_count", 0)
    if type(unavailable) is not int or not 0 <= unavailable <= 20:
        unavailable = 0
    session = db.get(AgentSession, message.session_id)
    if session is None or session.account_id != account.id:
        return [], len(stored) + unavailable
    readable: list[dict[str, Any]] = []
    for entry in stored:
        ref = (
            memory_sources.ExactChunkRef.parse(entry.get("_source_ref"))
            if isinstance(entry, dict)
            else None
        )
        resolved = (
            memory_sources.read_exact_chunk(
                db,
                ref,
                actor=account.user,
                account=account,
                space_id=session.space_id,
                agent_kind=session.agent_kind,
            )
            if ref
            else None
        )
        if (
            resolved is None
            or ref is None
            or entry.get("source_type") != ref.source_type
            or entry.get("source_id") != ref.source_id
            or entry.get("revision") != ref.source_revision
            or entry.get("citation_handle")
            != f"rag:{ref.source_id}:r{ref.source_revision}:c{ref.chunk_id}"
        ):
            unavailable += 1
            continue
        readable.append(
            {
                "source_type": ref.source_type,
                "source_id": ref.source_id,
                "revision": ref.source_revision,
                "scope": resolved.document.scope,
                "sensitivity": resolved.document.sensitivity,
                "citation_handle": entry["citation_handle"],
                "document_id": ref.document_id,
                "chunk_id": ref.chunk_id,
                "index_version": ref.index_version,
            }
        )
    return readable, unavailable


def fit_public_citations(
    base: dict[str, Any],
    citations: list[dict[str, Any]],
    unavailable: int,
) -> dict[str, Any]:
    """Preserve the original text/web byte contract; fallback is always available."""
    payload = public_base(base)

    def fits(value: dict[str, Any]) -> bool:
        return (
            len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            <= MAX_PAYLOAD_BYTES
        )

    for count in range(len(citations), -1, -1):
        proposed = {
            **payload,
            "citations": citations[:count],
            "unavailable_citation_count": unavailable,
            "citations_complete": count == len(citations),
        }
        if count < len(citations):
            proposed["omitted_citation_count"] = len(citations) - count
        if fits(proposed):
            return proposed
    # Body can occupy all 16 KiB. Lack of the complete marker triggers the
    # fixed run/seq fallback without truncating even one original text byte.
    return payload


def project_event_payload(
    db: Session,
    run: AgentRun,
    *,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
    account: Account,
) -> dict[str, Any]:
    base = public_base(payload)
    if event_type != "message.assistant_added":
        return base
    message = event_message(db, run, seq)
    citations, unavailable = project_message_citations(db, message, account) if message else ([], 0)
    return fit_public_citations(base, citations, unavailable)
