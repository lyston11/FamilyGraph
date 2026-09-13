"""V2.5 Memory and RAG HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ManualMemorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["manual"]


class AgentMessageMemorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["agent_message"]
    message_id: int = Field(gt=0)


class RAGChunkMemorySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rag_chunk"]
    document_id: int = Field(gt=0)
    chunk_id: int = Field(gt=0)
    revision: int = Field(gt=0)
    index_version: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    space_id: int = Field(gt=0)


MemorySource = Annotated[
    ManualMemorySource | AgentMessageMemorySource | RAGChunkMemorySource,
    Field(discriminator="kind"),
]


class MemoryCandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: MemorySource | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    source_message_id: int | None = Field(default=None, gt=0)
    source_document_ref: str | None = Field(default=None, max_length=255)
    source_span: dict[str, Any] = Field(default_factory=dict)
    raw_quote: str | None = Field(default=None, min_length=1, max_length=12_000)
    summary: str = Field(min_length=1, max_length=20_000)
    suggested_scope: Literal["private", "household", "lineage"] = "private"
    purpose: str = Field(min_length=1, max_length=120)
    sensitivity: Literal["normal", "sensitive", "high", "local_required"] = "normal"


class MemoryCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_message_id: int | None
    source_document_ref: str | None
    source_span_json: dict[str, Any]
    source_kind: Literal["manual", "agent_message", "rag_chunk", "legacy"]
    source_status: Literal["available", "deleted_snapshot", "unavailable", "unverified"]
    allowed_scopes: list[str]
    raw_quote: str | None
    summary: str | None
    suggested_scope: str
    purpose: str | None
    sensitivity: str
    extractor_version: str
    status: Literal["pending", "dismissed", "confirmed"]
    memory_id: int | None
    created_at: datetime
    decided_at: datetime | None


class MemoryConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(min_length=1, max_length=64)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_candidate_id: int | None
    source_message_id: int | None
    source_document_ref: str | None
    source_span_json: dict[str, Any]
    source_kind: Literal["manual", "agent_message", "rag_chunk", "legacy"]
    source_status: Literal["available", "deleted_snapshot", "unavailable", "unverified"]
    allowed_scopes: list[str]
    raw_quote: str | None
    content: str | None
    scope: str
    space_id: int | None
    sensitivity: str
    purpose: str | None
    confirmation_status: Literal["confirmed"]
    revision: int
    retention_until: datetime | None
    status: str
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RAGSearchOut(BaseModel):
    chunk_id: int
    document_id: int
    source_type: str
    source_id: str
    text: str
    scope: str
    sensitivity: str
    revision: int
    index_version: str
    citation_handle: str
    space_id: int | None
    allowed_scopes: list[str]
