"""Platform-level capability switches, kept separate from domain content."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlatformFeatureConfig(Base):
    """Singleton persisted after the first system-admin update."""

    __tablename__ = "platform_feature_configs"
    __table_args__ = (CheckConstraint("id = 1", name="ck_platform_feature_config_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rag_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 09-13：平台级 Steward 辅助开关（治理面与 memory/rag 一致；行缺失 = env 回退）
    steward_assist_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    steward_assist_ranking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    steward_assist_explanation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    steward_assist_terminology: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by_system_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_admins.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["PlatformFeatureConfig"]
