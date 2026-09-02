"""PersonalFamilyView and cross-lineage bridge projections.

The tables in this module are rebuildable authorization projections and bridge
consents. They are deliberately separate from SourceFact/Relation: neither a
view row nor a bridge row creates a relationship fact or a space membership.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

VIEW_STATUSES = ("never_computed", "queued", "running", "current", "stale", "failed")
BRIDGE_STATUSES = ("pending", "active", "revoked", "expired", "rejected")
VISIBILITY_LEVELS = ("self_private", "household_detail", "lineage_summary")

_VIEW_STATUS_SQL = f"status IN ({', '.join(repr(value) for value in VIEW_STATUSES)})"
_BRIDGE_STATUS_SQL = f"status IN ({', '.join(repr(value) for value in BRIDGE_STATUSES)})"
_VISIBILITY_LEVEL_SQL = (
    f"visibility_level IN ({', '.join(repr(value) for value in VISIBILITY_LEVELS)})"
)


class PersonalFamilyView(Base):
    """One rebuildable projection for one viewer/root/space authorization scope."""

    __tablename__ = "personal_family_views"
    __table_args__ = (
        CheckConstraint(_VIEW_STATUS_SQL, name="ck_pfv_status"),
        Index(
            "uq_pfv_scope",
            "viewer_account_id",
            "root_user_id",
            "space_id",
            unique=True,
        ),
        Index("ix_pfv_space_status", "space_id", "status"),
        Index("ix_pfv_viewer_space", "viewer_account_id", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    viewer_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    root_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="never_computed", nullable=False)
    view_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    computation_version: Mapped[str] = mapped_column(String(64), default="pfv-v1", nullable=False)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PersonalFamilyViewNode(Base):
    """Authorized node materialized for a PersonalFamilyView snapshot."""

    __tablename__ = "personal_family_view_nodes"
    __table_args__ = (
        CheckConstraint(_VISIBILITY_LEVEL_SQL, name="ck_pfv_node_visibility"),
        Index("uq_pfv_node_view_user", "view_id", "user_id", unique=True),
        Index("ix_pfv_node_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    view_id: Mapped[int] = mapped_column(
        ForeignKey("personal_family_views.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    display_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    visibility_level: Mapped[str] = mapped_column(String(20), nullable=False)
    inclusion_reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fact_ids_json: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    authorization_basis_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    computation_version: Mapped[str] = mapped_column(String(64), nullable=False)


class PersonalFamilyViewEdge(Base):
    """Authorized relationship edge in a view snapshot."""

    __tablename__ = "personal_family_view_edges"
    __table_args__ = (
        Index("ix_pfv_edge_view", "view_id"),
        Index("ix_pfv_edge_endpoints", "from_user_id", "to_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    view_id: Mapped[int] = mapped_column(
        ForeignKey("personal_family_views.id", ondelete="CASCADE"), nullable=False
    )
    from_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    to_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    edge_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_fact_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_facts.id", ondelete="SET NULL"), nullable=True
    )
    path_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    alternative_paths_json: Mapped[list[list[dict[str, Any]]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    path_class: Mapped[str] = mapped_column(String(32), nullable=False)
    concept_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    term: Mapped[str | None] = mapped_column(String(120), nullable=True)
    inclusion_reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_basis_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    computation_version: Mapped[str] = mapped_column(String(64), nullable=False)


class PersonalFamilyBridge(Base):
    """Two-person consent granting a minimal cross-lineage projection bridge."""

    __tablename__ = "personal_family_bridges"
    __table_args__ = (
        CheckConstraint(_BRIDGE_STATUS_SQL, name="ck_pfb_status"),
        CheckConstraint("lineage_space_a_id <> lineage_space_b_id", name="ck_pfb_distinct_spaces"),
        CheckConstraint("anchor_a_user_id <> anchor_b_user_id", name="ck_pfb_distinct_anchors"),
        Index("uq_pfb_normalized_pair", "normalized_pair_key", unique=True),
        Index("ix_pfb_space_status", "lineage_space_a_id", "status"),
        Index("ix_pfb_anchor_status", "anchor_a_user_id", "anchor_b_user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lineage_space_a_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    lineage_space_b_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    anchor_a_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    anchor_b_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    normalized_pair_key: Mapped[str] = mapped_column(String(255), nullable=False)
    initiated_by_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    consent_a_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    consent_b_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    consent_a_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consent_b_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "BRIDGE_STATUSES",
    "PersonalFamilyBridge",
    "PersonalFamilyView",
    "PersonalFamilyViewEdge",
    "PersonalFamilyViewNode",
    "VIEW_STATUSES",
    "VISIBILITY_LEVELS",
]
