"""users 表：家谱人员档案（PersonProfile）。

v2 Foundation（迁移 0008）语义：
- profile_status：provisional → identity_confirmed 单向状态机（identity_fsm.py 唯一转换点）
- is_admin 列已删除：平台角色存 platform_role_assignments（services/platform_roles.py）
- claim_status 列已删除：账号生命周期存 accounts.status
- clan_disclosure_json 已删除：披露偏好存 disclosure_preferences（全局+逐空间 scope）
deleted_at 为审计查询预留占位，v1 硬删除，不启用软删路径。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.account import Account

# v2 披露类别：基础五类（原 AD-9）+ 高敏感类（health/address/school/contact/private_notes，
# 对应未来档案字段；visibility 高风险 overlay 消费，任何层级不得自动开放）
DISCLOSURE_KEYS: tuple[str, ...] = (
    "avatar",
    "photos",
    "dates",
    "bio",
    "attachments",
    "health",
    "address",
    "school",
    "contact",
    "private_notes",
)
# 基础五类（对外披露开关端点兼容键集，v1 AD-9 形状）
BASIC_DISCLOSURE_KEYS: tuple[str, ...] = ("avatar", "photos", "dates", "bio", "attachments")
# 高敏感类：任何层级不得自动开放，披露端点恒拒绝 true（422），仅保留显式授权投影
HIGH_SENSITIVE_DISCLOSURE_KEYS: tuple[str, ...] = (
    "health",
    "address",
    "school",
    "contact",
    "private_notes",
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "profile_status IN ('provisional', 'identity_confirmed')",
            name="ck_users_profile_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # ---- 档案字段 ----
    gender: Mapped[str] = mapped_column(String(9), default="unknown", nullable=False)
    # 结构化生卒：{"cal_type":"solar|lunar|none","date":"YYYY-MM-DD|null","original_text":str}
    birth: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    death: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    privacy_mode: Mapped[str] = mapped_column(String(16), default="handover", nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ---- v2 确档状态机：provisional → identity_confirmed（单向，唯一转换点 identity_fsm）----
    profile_status: Mapped[str] = mapped_column(String(20), default="provisional", nullable=False)
    profile_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 与登录凭据 1:0..1：每个建档即配发 Account，业务层保证存在
    account: Mapped[Account] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} name={self.name!r} status={self.profile_status}>"
