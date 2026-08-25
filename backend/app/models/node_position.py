"""node_positions：画布手动位置记忆（m1d，UNIQUE(space,user)）。"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NodePosition(Base):
    __tablename__ = "node_positions"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_node_position_pair"),
        Index("ix_node_positions_space", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NodePosition space={self.space_id} user={self.user_id}>"
