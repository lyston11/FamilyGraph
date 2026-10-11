"""附件可确认内容版本与 Agent 使用授权的真源模型。

Attachment 仍是个人档案资源；空间只存在于 AgentContentGrant，不改变浏览器附件归属。
RAGDocument/RAGChunk 是由这些记录派生的可重建索引，不是授权真源。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.indexes import partial_unique_index

CONTENT_VERSION_KINDS = (
    "manual_text",
    "ocr_text",
    "link_metadata",
    "web_snapshot",
    "document_text",
)
CONTENT_VERSION_STATUSES = ("draft", "confirmed", "rejected", "superseded", "revoked")
GRANT_AGENT_KINDS = ("assistant",)
GRANT_PURPOSES = ("rag",)
GRANT_SCOPES = ("whole",)
GRANT_STATUSES = ("active", "revoked", "expired")


class AttachmentContentVersion(Base):
    """不可变的、由附件派生且经过用户确认的可引用文字。"""

    __tablename__ = "attachment_content_versions"
    __table_args__ = (
        UniqueConstraint("attachment_id", "revision", name="uq_acv_attachment_revision"),
        CheckConstraint(
            "content_kind IN ("
            "'manual_text','ocr_text','link_metadata','web_snapshot','document_text'"
            ")",
            name="ck_acv_content_kind",
        ),
        CheckConstraint(
            "status IN ('draft','confirmed','rejected','superseded','revoked')",
            name="ck_acv_status",
        ),
        CheckConstraint("revision >= 1", name="ck_acv_revision_positive"),
        CheckConstraint("length(text) > 0", name="ck_acv_text_nonempty"),
        CheckConstraint("length(content_sha256) = 64", name="ck_acv_hash_length"),
        Index("ix_acv_attachment_status", "attachment_id", "status"),
        Index("ix_acv_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentContentGrant(Base):
    """某个 household 空间对某个 Assistant 用途的明示授权。"""

    __tablename__ = "agent_content_grants"
    __table_args__ = (
        partial_unique_index(
            "uq_acg_active_grant",
            "content_version_id",
            "space_id",
            "agent_kind",
            "purpose",
            "grant_scope",
            where="status = 'active'",
        ),
        CheckConstraint("agent_kind IN ('assistant')", name="ck_acg_agent_kind"),
        CheckConstraint("purpose IN ('rag')", name="ck_acg_purpose"),
        CheckConstraint("grant_scope IN ('whole')", name="ck_acg_scope"),
        CheckConstraint("status IN ('active','revoked','expired')", name="ck_acg_status"),
        CheckConstraint("authorization_revision >= 1", name="ck_acg_revision_positive"),
        Index("ix_acg_content_version", "content_version_id"),
        Index("ix_acg_space_status", "space_id", "status"),
        Index("ix_acg_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    content_version_id: Mapped[int] = mapped_column(
        ForeignKey("attachment_content_versions.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="assistant")
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="rag")
    grant_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="whole")
    grantor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    grantor_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    authorization_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
