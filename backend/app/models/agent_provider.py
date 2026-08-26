"""Agent Provider 配置表（迁移 0009；notes.md：Provider 配置归后端）。

- agent_providers：platform_operator 维护的 Provider 注册
  （kind=openai_compatible|local，base_url、密钥密文、allowlist 模型）。
  密钥只存 secretbox 密文（utils/secretbox.py，SECRET_KEY 派生流加密），永不回明文。
- agent_space_provider_settings：空间级选择与开关（每空间至多一行）；
  policy 结果（cloud_allowed/local_required/denied）由 services/agent_provider.py 推导，
  不落库。ProviderGateway 与真实调用属后续 Block，本任务只提供配置与解析。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentProvider(Base):
    """Provider 注册：name 唯一；enabled=False 时该 Provider 全局不可解析。"""

    __tablename__ = "agent_providers"
    __table_args__ = (
        CheckConstraint("kind IN ('openai_compatible','local')", name="ck_agent_providers_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # secretbox 密文（nonce||ciphertext||tag 的 base64url）；本地 Provider 可无密钥
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_models_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentProvider {self.id} {self.name}/{self.kind} enabled={self.enabled}>"


class AgentSpaceProviderSetting(Base):
    """空间级 Provider 选择：space UNIQUE；policy 结果在服务层推导，不落库。"""

    __tablename__ = "agent_space_provider_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("agent_providers.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    cloud_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    local_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentSpaceProviderSetting space={self.space_id}"
            f" provider={self.provider_id} model={self.model}>"
        )
