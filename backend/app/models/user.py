"""users 表：家谱人员档案（PersonProfile，architecture.md §1 [AD-1]）。

m1a 增量列见迁移 0003：档案字段 + D5 归属模式 + ClaimState + AD-9 披露开关。
deleted_at 为审计查询预留占位，v1 硬删除，不启用软删路径。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.account import Account

# AD-9 披露开关五类；默认全部不公开
DISCLOSURE_KEYS: tuple[str, ...] = ("avatar", "photos", "dates", "bio", "attachments")


def default_disclosure() -> dict[str, bool]:
    """clan_disclosure_json 默认值（每次调用返回新 dict，避免可变共享）。"""
    return dict.fromkeys(DISCLOSURE_KEYS, False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # ---- 档案字段（m1a）----
    gender: Mapped[str] = mapped_column(String(9), default="unknown", nullable=False)
    # 结构化生卒：{"cal_type":"solar|lunar|none","date":"YYYY-MM-DD|null","original_text":str}
    birth: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    death: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # m3a 启用
    privacy_mode: Mapped[str] = mapped_column(String(16), default="handover", nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    claim_status: Mapped[str] = mapped_column(String(16), default="managed", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    clan_disclosure_json: Mapped[dict[str, bool]] = mapped_column(
        JSON, default=default_disclosure, nullable=False
    )

    # 与登录凭据 1:0..1（AD-1）：每个建档即配发 Account，业务层保证存在
    account: Mapped[Account] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def clan_disclosure(self) -> dict[str, bool]:
        """披露开关视图：缺失键按默认 false 兜底（防御历史数据缺键）。"""
        stored = self.clan_disclosure_json or {}
        return {key: bool(stored.get(key, False)) for key in DISCLOSURE_KEYS}

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} name={self.name!r}>"
