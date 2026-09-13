"""Steward 建议审核投影（任务 09-11-steward-candidate-review；迁移 0038）。

两张表：
- ``StewardSuggestion``：可审核建议（有证据的模型候选 / 确定性冲突待办的
  受控投影）。``steward_llm_candidates`` 仍是内部生成记录；本表是唯一对外
  审核面。去重键 = space + kind + 有向端点 + 结构化建议值（+ term_preference
  的 viewer account），**不含模型措辞/rationale**；``evidence_hash`` 单独成列，
  相同结构但证据变化 → 新建议 + supersede 旧活动行。部分唯一索引保证并发
  生成在同一 (space, dedupe_key, evidence_hash) 上收敛为一行。
- ``StewardSuggestionRecipient``：按收件人账号的已读/驳回/冷却独立状态
  （UNIQUE (suggestion_id, account_id)）；驳回是收件人独立状态，不终结建议。

状态机：proposed → submitted → resolved；proposed/submitted 可 expired /
superseded（终态不可复活）。submitted 的终局由关联领域对象决定。
evidence_json 只允许存白名单 ID/revision（fact/candidate），绝不存模型
自由文本（rationale 在 quality-security 校验层已被丢弃）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SUGGESTION_ORIGINS = ("deterministic", "model")

SUGGESTION_KINDS = (
    "relation_proposal",
    "term_preference",
    "identity_duplicate",
    "missing_information",
)

SUGGESTION_STATES = ("proposed", "submitted", "resolved", "expired", "superseded")
SUGGESTION_ACTIVE_STATES = ("proposed", "submitted")

_ORIGIN_CHECK_SQL = f"origin IN ({', '.join(repr(o) for o in SUGGESTION_ORIGINS)})"
_KIND_CHECK_SQL = f"kind IN ({', '.join(repr(k) for k in SUGGESTION_KINDS)})"
_STATUS_CHECK_SQL = f"status IN ({', '.join(repr(s) for s in SUGGESTION_STATES)})"

# 去重唯一性的活跃态集合：并发生成收敛、同证据不重复
_ACTIVE_STATUS_SQL = "status IN ('proposed','submitted')"


class StewardSuggestion(Base):
    """One reviewable steward suggestion projection row."""

    __tablename__ = "steward_suggestions"
    __table_args__ = (
        CheckConstraint(_ORIGIN_CHECK_SQL, name="ck_ss_origin"),
        CheckConstraint(_KIND_CHECK_SQL, name="ck_ss_kind"),
        CheckConstraint(_STATUS_CHECK_SQL, name="ck_ss_status"),
        # 并发生成收敛：同 (space, dedupe_key, evidence_hash) 至多一条活跃建议
        Index(
            "uq_steward_suggestions_active_dedupe",
            "space_id",
            "dedupe_key",
            "evidence_hash",
            unique=True,
            sqlite_where=text(_ACTIVE_STATUS_SQL),
        ),
        Index("ix_steward_suggestions_space_status", "space_id", "status"),
        Index("ix_steward_suggestions_subject", "subject_user_id"),
        Index("ix_steward_suggestions_object", "object_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    object_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # term_preference 的归属账号（本人显式提交；NULL=非个人维度建议）
    viewer_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    # 结构化建议值（封闭：relation 的 fact_type / term 的 concept_code /
    # finding 的 code）；绝不承载模型自由文本
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # 证据快照：只允许白名单 ID/revision（facts:[{id,revision}], candidates:[id]）
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 生成来源关联（SET NULL：内部候选行/作业清除不影响审核投影）
    source_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_llm_candidates.id", ondelete="SET NULL"), nullable=True
    )
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_jobs.id", ondelete="SET NULL"), nullable=True
    )
    # 领域关联（逻辑引用不设 FK，避免级联环；提交后由服务层回填）
    linked_fact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    linked_term_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 提交幂等：同 Idempotency-Key 重试返回同一关联对象
    submit_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    submit_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardSuggestion {self.id} space={self.space_id} {self.kind}"
            f"/{self.status} r{self.revision}>"
        )


class StewardSuggestionRecipient(Base):
    """Per-recipient read/dismiss/cooldown state for a suggestion."""

    __tablename__ = "steward_suggestion_recipients"
    __table_args__ = (
        Index(
            "uq_steward_suggestion_recipients",
            "suggestion_id",
            "account_id",
            unique=True,
        ),
        Index("ix_steward_suggestion_recipients_account", "account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(
        ForeignKey("steward_suggestions.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 驳回冷却只作用于该收件人的同一证据版本（证据变更 supersede 后冷却失效）
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cooldown_evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 有限偏好反馈（09-13 terminology）：kept=保留为我的叫法 / restored=恢复默认
    preference_feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)
    preference_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StewardSuggestionRecipient suggestion={self.suggestion_id}"
            f" account={self.account_id} dismissed={self.dismissed_at is not None}>"
        )


__all__ = [
    "SUGGESTION_ACTIVE_STATES",
    "SUGGESTION_KINDS",
    "SUGGESTION_ORIGINS",
    "SUGGESTION_STATES",
    "StewardSuggestion",
    "StewardSuggestionRecipient",
]
