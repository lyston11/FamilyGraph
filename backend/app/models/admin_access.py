"""系统管理员访问会话与独立审计表（09-04 子任务 2，迁移 0029）。

- admin_access_sessions：敏感详情访问票据。绑定单个 user 或 space，TTL 30 分钟；
  数据库只存 ``token_hash``（SHA-256 hex），明文票据只出现在创建响应中。
- admin_access_audits：独立于家庭 ``audit_log``（其 actor_id FK 指向家庭 users，
  无法表达 system_admin 主体）。两表永久保留：业务对象删除不级联删除审计行
  （audits.system_admin_id/session_id 均为 SET NULL），审计正文只含理由/范围/
  数量/请求信息，不含响应正文、密码、token、原始错误或私密文本。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ADMIN_ACCESS_TARGET_TYPES = ("user", "space")


class AdminAccessSession(Base):
    """绑定单目标的管理员访问会话（票据 hash 持久化，可撤销）。"""

    __tablename__ = "admin_access_sessions"
    __table_args__ = (
        CheckConstraint("target_type IN ('user','space')", name="ck_admin_access_session_target"),
        Index("ix_admin_access_sessions_admin", "system_admin_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    system_admin_id: Mapped[int] = mapped_column(
        ForeignKey("system_admins.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AdminAccessSession {self.id} admin={self.system_admin_id}"
            f" {self.target_type}={self.target_id}>"
        )


class AdminAccessAudit(Base):
    """后台读取/会话/审批审计：只写理由、范围、数量与请求元信息。"""

    __tablename__ = "admin_access_audits"
    __table_args__ = (
        CheckConstraint(
            "target_type IS NULL OR target_type IN ('user','space')",
            name="ck_admin_access_audit_target",
        ),
        Index("ix_admin_access_audits_created_at", "created_at"),
        Index("ix_admin_access_audits_admin", "system_admin_id"),
        Index("ix_admin_access_audits_target", "target_type", "target_id"),
        Index("ix_admin_access_audits_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    system_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_admins.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_access_sessions.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AdminAccessAudit {self.id} {self.action!r} endpoint={self.endpoint!r}>"
