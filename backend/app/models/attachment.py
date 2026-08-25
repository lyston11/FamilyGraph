"""附件模型（m3a，AD-7 安全边界）。location 仅枚举预留。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("type IN ('image','link','location')", name="ck_att_type"),
        Index("ix_attachments_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    url_or_path: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Attachment {self.id} {self.type} user={self.user_id}>"
