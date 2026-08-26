"""V2 Foundation 合同表（迁移 0008_v2_foundation）。

本模块集中 v2 新增的九张合同表（spec/architecture.md §0）：
- PlatformRoleAssignment：平台角色（仅 platform_operator），取代 users.is_admin
- OwnerInvitation：owner onboarding link（只存 token hash，单次/可撤销/过期）
- DisclosurePreference：字段级披露偏好（全局或逐空间 scope，默认不公开）
- OwnershipTransfer：owner 移交 FSM（pending → accepted/cancelled/expired）
- ClaimDispute：认领争议（保留证据，平台人工兜底需审计）
- DataRightRequest：自助导出/更正/删除请求状态机
- DomainEvent：append-only 领域事件（投影失效与 Agent 后续消费的稳定事实）
- ProfileFactReview：确档清单逐项确认/争议（proposed → confirmed | disputed）

append-only 约定：DomainEvent 按惯例只追加，不做 UPDATE/DELETE。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.user import DISCLOSURE_KEYS

_CATEGORY_CHECK_SQL = f"category IN ({', '.join(repr(k) for k in DISCLOSURE_KEYS)})"


class PlatformRoleAssignment(Base):
    """平台角色：v2 仅 platform_operator；与家庭数据读取权完全无关。"""

    __tablename__ = "platform_role_assignments"
    __table_args__ = (
        CheckConstraint(
            "role IN ('platform_operator')",
            name="ck_pra_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PlatformRoleAssignment account={self.account_id} role={self.role}>"


class OwnerInvitation(Base):
    """owner onboarding link：短期、单次、可撤销；数据库只保存 token hash。"""

    __tablename__ = "owner_invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OwnerInvitation {self.id} used={self.used_at is not None}>"


class DisclosurePreference(Base):
    """披露偏好：scope='global' 时 space_id 必为 NULL，scope='space' 时必填。

    SQLite UNIQUE 不把 NULL 视为相等，故用表达式唯一索引 COALESCE(space_id,-1)
    保证同一 (profile, category) 的全局行至多一条。
    """

    __tablename__ = "disclosure_preferences"
    __table_args__ = (
        CheckConstraint(_CATEGORY_CHECK_SQL, name="ck_dp_category"),
        CheckConstraint("scope IN ('global', 'space')", name="ck_dp_scope"),
        CheckConstraint(
            "(scope = 'global' AND space_id IS NULL) OR (scope = 'space' AND space_id IS NOT NULL)",
            name="ck_dp_scope_pair",
        ),
        Index(
            "uq_disclosure_pref_scope",
            "profile_id",
            "category",
            sa.text("COALESCE(space_id, -1)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(10), default="global", nullable=False)
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DisclosurePreference profile={self.profile_id}"
            f" {self.category}/{self.scope}={self.allowed}>"
        )


class OwnershipTransfer(Base):
    """owner 移交 FSM：pending → accepted/cancelled/expired；同空间同时至多一个 pending。"""

    __tablename__ = "ownership_transfers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','cancelled','expired')", name="ck_ot_status"
        ),
        Index(
            "uq_ownership_transfer_active",
            "space_id",
            unique=True,
            sqlite_where=sa.text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    from_user: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    to_user: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OwnershipTransfer space={self.space_id}"
            f" {self.from_user}->{self.to_user} {self.status}>"
        )


class ClaimDispute(Base):
    """认领争议：保留证据原文，处理结果走 resolved_claim/resolved_reject。"""

    __tablename__ = "claim_disputes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','resolved_claim','resolved_reject','withdrawn')", name="ck_cd_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    raised_by_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ClaimDispute profile={self.profile_id} {self.status}>"


class DataRightRequest(Base):
    """数据权利请求：export/correct/delete 统一状态机；异步产物继承可见性策略并过期。"""

    __tablename__ = "data_right_requests"
    __table_args__ = (
        CheckConstraint("type IN ('export','correct','delete')", name="ck_drr_type"),
        CheckConstraint(
            "status IN ('pending','processing','completed','rejected','expired')",
            name="ck_drr_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requestor_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    subject_profile_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    scope: Mapped[str] = mapped_column(String(64), default="self", nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DataRightRequest {self.type}/{self.status} subject={self.subject_profile_id}>"


class DomainEvent(Base):
    """领域事件：单调递增 id、按 (aggregate_type, aggregate_id) 索引；append-only。"""

    __tablename__ = "domain_events"
    __table_args__ = (Index("ix_domain_events_aggregate", "aggregate_type", "aggregate_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="SET NULL"), nullable=True
    )
    actor_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DomainEvent {self.id} {self.type} {self.aggregate_type}#{self.aggregate_id}>"


class ProfileFactReview(Base):
    """确档清单逐项模型：proposed → confirmed | disputed（单向，终态不可再转）。"""

    __tablename__ = "profile_fact_reviews"
    __table_args__ = (
        CheckConstraint("status IN ('proposed','confirmed','disputed')", name="ck_pfr_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    item_ref_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    proposed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProfileFactReview profile={self.profile_id} {self.item_type}:{self.status}>"
