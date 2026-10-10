"""Platform-level capability switches, kept separate from domain content."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
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
    # 管家可读的记忆级别（规范化编码，逗号分隔，值域 MEMORY_SCOPES 的子集；
    # 空串 = 空集）。有效值 = env ∩ 本列 ∩ 空间列，见 services/steward_memory.py。
    # 默认空 = 管家读不到任何记忆，与引入本列之前的行为一致。
    steward_memory_scopes: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by_system_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_admins.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["PlatformFeatureConfig"]
