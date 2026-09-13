"""Steward 推测层（任务 09-13-steward-inferred-tree-layer；迁移 0042）。

``StewardInferredEdge``：管家推断的「推测关系边」——LLM 候选
（``steward_llm_candidates``，节点代号白名单）经管家作业投影的空间级单跳
原子关系建议。设计红线（design.md）：

- **显示层投影，不是事实层**：推测边永不写 SourceFact；确认走
  ``relationship_proposals`` 现行 consent 合同（有权当事人直接转正，
  无权仅 202 提案）。
- 单跳原子关系（kind 白名单与 ``steward_guard.validate_candidate_output``
  一致）；推测边之间不互联（增广图只含 proposed 单跳），避免多跳不确定性
  复合。
- 证据口径：``evidence_hash`` = 投影时 facts brief 摘要；confirmed facts
  变化 → 旧行 superseded + 新行；同三元组 rejected 且同证据 → 冷却跳过。
- 状态机：proposed → confirmed / rejected / superseded；rejected 可
  reinstate 回 proposed（无同三元组活跃行且不超上限）。confirmed/superseded
  为终态，不再渲染。

relation_kind 白名单（与 candidate 输出校验一致）：
biological_parent / adoptive_parent / step_parent / guardian / spouse /
partner / direct_sibling。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

INFERRED_ORIGINS = ("llm", "rule", "intake")

INFERRED_RELATION_KINDS = (
    "biological_parent",
    "adoptive_parent",
    "step_parent",
    "guardian",
    "spouse",
    "partner",
    "direct_sibling",
)

INFERRED_STATES = ("proposed", "rejected", "confirmed", "superseded")
INFERRED_ACTIVE_STATE = "proposed"

_ORIGIN_CHECK_SQL = f"origin IN ({', '.join(repr(o) for o in INFERRED_ORIGINS)})"
_KIND_CHECK_SQL = f"relation_kind IN ({', '.join(repr(k) for k in INFERRED_RELATION_KINDS)})"
_STATUS_CHECK_SQL = f"status IN ({', '.join(repr(s) for s in INFERRED_STATES)})"


class StewardInferredEdge(Base):
    """空间级推测关系边（单跳原子关系建议；显示层投影，永不写事实层）。"""

    __tablename__ = "steward_inferred_edges"
    __table_args__ = (
        CheckConstraint(_ORIGIN_CHECK_SQL, name="ck_sie_origin"),
        CheckConstraint(_KIND_CHECK_SQL, name="ck_sie_kind"),
        CheckConstraint(_STATUS_CHECK_SQL, name="ck_sie_status"),
        CheckConstraint("subject_user_id <> object_user_id", name="ck_sie_distinct_endpoints"),
        # 每三元组至多一条活跃推测（去重 + 冷却 + 幂等投影的兜底约束）
        Index(
            "uq_sie_active_triple",
            "space_id",
            "subject_user_id",
            "object_user_id",
            "relation_kind",
            unique=True,
            sqlite_where=text(f"status = '{INFERRED_ACTIVE_STATE}'"),
        ),
        Index("ix_sie_space_status", "space_id", "status"),
        Index("ix_sie_endpoints", "subject_user_id", "object_user_id"),
        Index("ix_sie_candidate", "source_candidate_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    subject_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    object_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    relation_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default="llm", nullable=False)
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_llm_candidates.id", ondelete="SET NULL"), nullable=True
    )
    # 投影时 facts brief 摘要（steward_assist._canonical_hash 同口径）；驳回
    # 冷却与证据变化 supersede 的判定键。evidence_json 只存白名单
    # fact id/revision 快照（无姓名/原文），与 suggestion evidence 同红线。
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardInferredEdge {self.id} space={self.space_id}"
            f" {self.subject_user_id}-{self.relation_kind}->{self.object_user_id}"
            f" {self.status} r{self.revision}>"
        )
