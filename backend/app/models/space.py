"""家庭空间与成员资格、provisional 引用模型（v2 Foundation，原 m1c AD-3/AD-4）。

v2 语义：
- family_spaces.kind = household | lineage；owner FK 为 RESTRICT：
  删除 owner 前必须移交/显式终止，禁止 FK 级联静默删空间。
- space_members.role 扩展为 owner|space_admin|member|guest；guest 不获得
  household_detail（visibility.py 消费）。
- space_profile_refs：创建他人选择空间时只建最小节点引用；
  provisional 人物不是 SpaceMember。
"""

from __future__ import annotations

from datetime import datetime

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

SPACE_MEMBER_STATUSES = ("pending", "active", "rejected", "withdrawn", "removed")
SPACE_MEMBER_ROLES = ("owner", "space_admin", "member", "guest")
PENDING_EXPIRY_DAYS = 30


class FamilySpace(Base):
    __tablename__ = "family_spaces"
    __table_args__ = (
        CheckConstraint("kind IN ('household', 'lineage')", name="ck_family_spaces_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # v2：RESTRICT —— owner 行删除被数据库拒绝，移交/终止流程负责显式处理
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), default="household", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FamilySpace {self.id} {self.name!r} kind={self.kind} owner={self.owner_id}>"


class SpaceMember(Base):
    __tablename__ = "space_members"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_space_member_pair"),
        CheckConstraint("role IN ('owner','space_admin','member','guest')", name="ck_sm_role"),
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
        return (
            f"<SpaceMember space={self.space_id} user={self.user_id}"
            f" role={self.role} {self.status}>"
        )


class SpaceProfileRef(Base):
    """space_profile_refs：provisional 人物在空间中的最小节点引用（v2 F-3）。

    被引用者不是 SpaceMember、不进入推荐资格；可见性仅 lineage_summary 基线。
    """

    __tablename__ = "space_profile_refs"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_space_profile_ref_pair"),
        CheckConstraint("status IN ('active', 'removed')", name="ck_spr_status"),
        Index("ix_space_profile_refs_space", "space_id"),
        Index("ix_space_profile_refs_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SpaceProfileRef space={self.space_id} user={self.user_id} {self.status}>"
