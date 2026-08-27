"""Controlled Web configuration, approved tokens, usage and citation records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WebPlatformConfig(Base):
    """Singleton platform gate; no family data is stored here."""

    __tablename__ = "web_platform_configs"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_web_platform_config_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    search_provider: Mapped[str] = mapped_column(String(64), default="configured", nullable=False)
    search_endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_domains_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    denied_domains_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_results: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_fetch_bytes: Mapped[int] = mapped_column(Integer, default=1_000_000, nullable=False)
    max_requests_per_minute: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    monthly_budget_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )


class WebSpaceConfig(Base):
    """Space-local opt-in and ceilings; it can only narrow platform policy."""

    __tablename__ = "web_space_configs"
    __table_args__ = (
        CheckConstraint("max_results >= 1 AND max_results <= 50", name="ck_web_space_max_results"),
        CheckConstraint("max_fetch_bytes > 0", name="ck_web_space_fetch_bytes"),
        CheckConstraint("max_requests_per_minute > 0", name="ck_web_space_rate"),
        Index("uq_web_space_configs_space", "space_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_use_cases_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_results: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_fetch_bytes: Mapped[int] = mapped_column(Integer, default=1_000_000, nullable=False)
    max_requests_per_minute: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    monthly_budget_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )


class WebApprovedURL(Base):
    """One-use fetch capability issued by a successful search."""

    __tablename__ = "web_approved_urls"
    __table_args__ = (
        CheckConstraint("used_at IS NULL OR used_at >= created_at", name="ck_web_token_used_after_create"),
        Index("ix_web_approved_urls_scope", "space_id", "account_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebRequestUsage(Base):
    """Privacy-minimized operational record; raw queries/payloads are never stored."""

    __tablename__ = "web_request_usage"
    __table_args__ = (
        CheckConstraint("tool IN ('search_web','fetch_approved_page')", name="ck_web_usage_tool"),
        Index("ix_web_usage_scope_time", "space_id", "account_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    query_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bytes_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class WebCitation(Base):
    """Citations are linked to a run/message and contain only bounded excerpts."""

    __tablename__ = "web_citations"
    __table_args__ = (
        Index("ix_web_citations_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trust: Mapped[str] = mapped_column(String(16), default="external", nullable=False)
