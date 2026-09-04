"""独立系统管理员主体与凭据（09-04 密码合同）。

该模型故意不引用 User、Account、FamilySpace 或 SpaceMember。系统管理员只
拥有平台治理主体，家庭用户端依赖会明确拒绝此主体类型。

凭据合同（SF-F2）：用户名 + 强密码；数据库只存 ``password_hash`` 与版本/锁定
字段，密码明文只出现在 0600 bootstrap 凭据文件中的一次性交付，不进日志/审计。
旧 PIN 结构（pin_hash/pin_must_change/token_version）已由迁移 0028 移除，
不做兼容（迁移对存量 PIN 行 fail-closed，见 migrations 0028）。
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

SYSTEM_ADMIN_USERNAME_MAX_LENGTH = 100


class SystemAdmin(Base):
    __tablename__ = "system_admins"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="ck_system_admin_status"),
        Index("ix_system_admins_username", "username", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(SYSTEM_ADMIN_USERNAME_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    account: Mapped["SystemAdminAccount"] = relationship(
        back_populates="system_admin", uselist=False, cascade="all, delete-orphan"
    )


class SystemAdminAccount(Base):
    __tablename__ = "system_admin_accounts"
    __table_args__ = (
        CheckConstraint("status IN ('managed','claimed')", name="ck_system_admin_account_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    system_admin_id: Mapped[int] = mapped_column(
        ForeignKey("system_admins.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_must_change: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    password_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="managed", nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    system_admin: Mapped[SystemAdmin] = relationship(back_populates="account")


class SystemAdminRefreshSession(Base):
    __tablename__ = "system_admin_refresh_sessions"
    __table_args__ = (Index("ix_system_admin_refresh_sessions_admin", "system_admin_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    system_admin_id: Mapped[int] = mapped_column(
        ForeignKey("system_admins.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    rotated_from: Mapped[int | None] = mapped_column(
        ForeignKey("system_admin_refresh_sessions.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
