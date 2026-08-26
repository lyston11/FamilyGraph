"""DerivedFact 缓存表（迁移 0011_derived_facts，V2.3 Block E2）。

可重建投影而非真源：真源是 SourceFact（models/relationship_facts.py）。
(viewer_user_id, target_user_id, space_id) 唯一；evidence_hash 是缓存新鲜度
判据，SourceFact 快照或算法版本变化后旧行由 services/derived_facts.py
重算/删除。term_version 由 E3 TermRegistry 填充，E2 恒为 NULL。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DerivedFact(Base):
    """viewer 在 space 内对 target 的确定性亲属结论缓存（AC-KI7/8）。

    main_path_json / alt_paths_json 存规范化 step 序列（合同见
    services/relationship_resolver.py）；evidence_fact_ids_json 是主路径引用的
    confirmed SourceFact id 列表；evidence_hash = sha256(snapshot_hash +
    algorithm_version)，读取时比较以拒绝过期缓存。
    """

    __tablename__ = "derived_facts"
    __table_args__ = (
        Index(
            "uq_derived_facts_viewer_target_space",
            "viewer_user_id",
            "target_user_id",
            "space_id",
            unique=True,
        ),
        Index("ix_derived_facts_viewer", "viewer_user_id"),
        Index("ix_derived_facts_target", "target_user_id"),
        Index("ix_derived_facts_space", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    viewer_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    concept_code: Mapped[str] = mapped_column(String(128), nullable=False)
    main_path_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    alt_paths_json: Mapped[list[list[dict[str, Any]]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    evidence_fact_ids_json: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(16), nullable=False)
    term_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DerivedFact v={self.viewer_user_id} t={self.target_user_id}"
            f" s={self.space_id} {self.concept_code} {self.evidence_hash[:8]}>"
        )
