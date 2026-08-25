"""users 表：最小身份列（id/name/is_admin/created_at）。

档案字段（gender/birth/death/bio/privacy_mode/claim_status 等）由 m1a 增量迁移引入，
禁止一次建全（m0b 范围约束）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.account import Account


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 与登录凭据 1:0..1（AD-1）：每个建档即配发 Account，业务层保证存在
    account: Mapped[Account] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} name={self.name!r}>"
