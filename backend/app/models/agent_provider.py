"""Agent Provider 配置表（迁移 0009；notes.md：Provider 配置归后端）。

- agent_providers：platform_operator 维护的 Provider 注册
  （kind=openai_compatible|local，base_url、密钥密文、allowlist 模型）。
  密钥只存 secretbox 密文（utils/secretbox.py，SECRET_KEY 派生流加密），永不回明文。
- agent_space_provider_settings：空间级选择与开关（每空间每 agent_kind 至多一行，
  唯一键 (space_id, agent_kind)；agent_kind=assistant|steward，迁移 0033）；
  policy 结果（cloud_allowed/local_required/denied）由 services/agent_provider.py 推导，
  不落库。ProviderGateway 在服务端使用该解析结果执行唯一 egress；运行期元数据
  由 AgentRun.runtime_snapshot_json 固化，密钥仍只在网关解密。
- agent_platform_defaults：平台级默认模型（单行表，迁移 0033）。管理员维护
  每 agent_kind 的默认 provider+model；owner 未显式选择时由服务层回退到此，
  云同意仍归空间（cloud_allowed 默认 False 不变）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentProvider(Base):
    """Provider 注册：name 唯一；enabled=False 时该 Provider 全局不可解析。"""

    __tablename__ = "agent_providers"
    __table_args__ = (
        CheckConstraint("kind IN ('openai_compatible','local')", name="ck_agent_providers_kind"),
        CheckConstraint(
            "api IN ('openai-completions','openai-responses')", name="ck_agent_providers_api"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Pi provider protocol (for example openai-responses or openai-completions).
    # Kept server-side so the run snapshot and gateway use the same adapter.
    api: Mapped[str] = mapped_column(String(48), default="openai-responses", nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    compat_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    context_window: Mapped[int] = mapped_column(Integer, default=272_000, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=60_000, nullable=False)
    reasoning: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    input_modalities_json: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["text", "image"], nullable=False
    )
    thinking_levels_json: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["low", "medium", "high", "xhigh", "max"], nullable=False
    )
    # secretbox 密文（nonce||ciphertext||tag 的 base64url）；本地 Provider 可无密钥
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_models_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentProvider {self.id} {self.name}/{self.kind} enabled={self.enabled}>"


class AgentSpaceProviderSetting(Base):
    """空间级 Provider 选择：(space_id, agent_kind) UNIQUE；policy 结果在服务层推导，不落库。

    provider_id/model 可空：enabled=False 的显式停用行允许无选择（owner 明确
    停用继承时不强制选型，resolve 走 setting_disabled；design §3.2）。
    """

    __tablename__ = "agent_space_provider_settings"
    __table_args__ = (
        CheckConstraint("agent_kind IN ('assistant','steward')", name="ck_asps_agent_kind"),
        UniqueConstraint("space_id", "agent_kind", name="uq_asps_space_agent"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_kind: Mapped[str] = mapped_column(String(32), default="assistant", nullable=False)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_providers.id", ondelete="CASCADE"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cloud_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    local_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 09-06 Steward 模型辅助层空间级开关（仅 steward 维度消费；有效开关 =
    # 平台 config 开关 AND 空间列，默认全关 → 行为与确定性基线等价）
    assist_candidate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assist_ranking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assist_explanation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 09-13 称谓自主优化空间级开关（与平台 steward_assist_terminology 相与）
    assist_terminology: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 09-13 Steward 推测层空间级开关（有效开关 = 平台 config 开关 AND 本列，
    # 默认关 → 无推测投影、PFV 无推测区块）
    inferred_tree: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentSpaceProviderSetting space={self.space_id} kind={self.agent_kind}"
            f" provider={self.provider_id} model={self.model}>"
        )


class AgentPlatformDefault(Base):
    """平台级默认模型（单行）：owner 未显式选择时按 agent_kind 回退；云同意仍归空间。"""

    __tablename__ = "agent_platform_defaults"
    __table_args__ = (CheckConstraint("id = 1", name="ck_agent_platform_default_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    assistant_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_providers.id", ondelete="SET NULL"), nullable=True
    )
    assistant_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    steward_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_providers.id", ondelete="SET NULL"), nullable=True
    )
    steward_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_admins.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentPlatformDefault assistant={self.assistant_provider_id}"
            f" steward={self.steward_provider_id}>"
        )
