"""V2.3 Relationship Intelligence 事实层合同表（迁移 0010_relationship_facts）。

三张表（跨块合同，E2 路径解析 / E3 TermRegistry 均按此引用，列名与枚举勿改）：
- SourceFact：稳定原子亲属事实（KI-1）。方向语义：fact_type 表达 subject
  相对 object 的角色——*_parent 四类 subject 是 object 的父/母/监护人；
  spouse/partner/direct_sibling 为对称关系，subject 为申报主体。
- SocialRelation：friend/colleague 等社会关系，单独存储，不参加血缘/姻亲
  路径计算（E2 不消费本表）。
- RawRelationInput：自由输入原文（KI-3）。append-only，任何词典/Agent 产物
  不得覆盖——不可变性由数据库触发器 trg_raw_relation_inputs_immutable 强制。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ---- 枚举常量（服务层与迁移共用；CHECK 约束兜底）----
SOURCE_FACT_TYPES = (
    "biological_parent",
    "adoptive_parent",
    "step_parent",
    "guardian",
    "spouse",
    "partner",
    "direct_sibling",
)
# 参与世代层级（成环检测）的 parent 类事实
PARENT_FACT_TYPES = ("biological_parent", "adoptive_parent", "step_parent", "guardian")
SOURCE_FACT_STATES = ("proposed", "confirmed", "disputed", "revoked")
SOURCE_FACT_PROVENANCES = (
    "profile_form",
    "connection_accept",
    "manual_entry",
    "import",
    "agent_proposal",
)
SOCIAL_RELATION_KINDS = ("friend", "colleague", "acquaintance", "other")

_RAW_TEXT_MAX_LENGTH = 200


def _check_in(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


class SourceFact(Base):
    """原子亲属事实：proposed → confirmed | disputed；confirmed/disputed → revoked。

    同 (subject, object, fact_type, space_id) 至多一条非 revoked 行
    （partial unique index，space 用 COALESCE 归一 NULL）；parent 类成环
    检测在服务层（services/source_facts.py，沿 confirmed 边上溯 ≤32 层）。
    """

    __tablename__ = "source_facts"
    __table_args__ = (
        _check_in("fact_type", SOURCE_FACT_TYPES, "ck_sf_fact_type"),
        _check_in("state", SOURCE_FACT_STATES, "ck_sf_state"),
        _check_in("provenance", SOURCE_FACT_PROVENANCES, "ck_sf_provenance"),
        CheckConstraint("subject_user_id != object_user_id", name="ck_sf_no_self"),
        Index(
            "uq_source_facts_active",
            "subject_user_id",
            "object_user_id",
            "fact_type",
            sa.text("COALESCE(space_id, -1)"),
            unique=True,
            sqlite_where=sa.text("state != 'revoked'"),
        ),
        Index("ix_source_facts_subject", "subject_user_id"),
        Index("ix_source_facts_object", "object_user_id"),
        Index("ix_source_facts_space_state", "space_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 方向合同：*_parent 类 subject 是 object 的父/母/监护人（见模块 docstring）
    subject_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    object_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # NULL = 全局事实（跨空间成立）；否则仅在该空间内消费
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    asserted_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    raw_text_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_relation_inputs.id", ondelete="SET NULL"), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SourceFact {self.id} {self.fact_type} {self.subject_user_id}"
            f"->{self.object_user_id} {self.state} r{self.revision}>"
        )


class SocialRelation(Base):
    """社会关系（friend/colleague 等）：仅存储，不参与血缘/姻亲路径与推荐。"""

    __tablename__ = "social_relations"
    __table_args__ = (
        _check_in("relation_kind", SOCIAL_RELATION_KINDS, "ck_sr_kind"),
        CheckConstraint("user_a_id != user_b_id", name="ck_sr_no_self"),
        Index("ix_social_relations_user_a", "user_a_id"),
        Index("ix_social_relations_user_b", "user_b_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    relation_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    user_a_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_b_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SocialRelation {self.id} {self.relation_kind}" f" {self.user_a_id}~{self.user_b_id}>"
        )


class RawRelationInput(Base):
    """自由输入原文：append-only，无更新路径（数据库触发器强制），KI-3 红线。"""

    __tablename__ = "raw_relation_inputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(String(_RAW_TEXT_MAX_LENGTH), nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RawRelationInput {self.id} author={self.author_account_id}>"
