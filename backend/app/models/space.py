"""家庭空间与成员资格模型（m1c，AD-3/AD-4）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

if TYPE_CHECKING:
    pass

SPACE_MEMBER_STATUSES = ("pending", "active", "rejected", "withdrawn", "removed")
PENDING_EXPIRY_DAYS = 30


class FamilySpace(Base):
    __tablename__ = "family_spaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FamilySpace {self.id} {self.name!r} owner={self.owner_id}>"


class SpaceMember(Base):
    __tablename__ = "space_members"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_space_member_pair"),
        CheckConstraint("role IN ('owner','member')", name="ck_sm_role"),
        CheckConstraint(
            "status IN ('pending','active','rejected','withdrawn','removed')",
            name="ck_sm_status",
        ),
        Index("ix_space_members_space", "space_id"),
        Index("ix_space_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SpaceMember space={self.space_id} user={self.user_id} {self.status}>"
