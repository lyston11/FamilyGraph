"""relations 表：四分类关系边（architecture.md §4 FSM / §5 DB 契约 [D2]）。

方向语义：to_user 是 from_user 的 dir_class（创建者视角）。
反向显示不存储，由 services/kinship.py 动态推导（D3）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

if TYPE_CHECKING:
    pass

DIR_CLASSES = ("elder", "younger", "peer", "spouse")
# 直系结构边：唯一决定树形骨架 + 完整数据互见（QU1=B）
STRUCTURAL_CLASSES = ("elder", "younger", "spouse")
STATUSES = ("pending", "active", "rejected", "cancelled", "revoked")
NON_TERMINAL_STATUSES = ("pending", "active")


class Relation(Base):
    __tablename__ = "relations"
    __table_args__ = (
        Index(
            "uq_relations_pair_fwd",
            "from_user",
            "to_user",
            unique=True,
            sqlite_where=text("status IN ('pending','active')"),
        ),
        Index(
            "uq_relations_pair_rev",
            "to_user",
            "from_user",
            unique=True,
            sqlite_where=text("status IN ('pending','active')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_user: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_user: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dir_class: Mapped[str] = mapped_column(String(16), nullable=False)
    # 创建者视角自由称谓（如"三叔公"），仅展示检索用（D3）
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 视角归属人（=from_user，冗余留审计与查询）
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Relation {self.from_user}->{self.to_user} {self.dir_class} {self.status}>"
