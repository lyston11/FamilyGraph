"""面向收件人的最小通知投影（任务 09-02-personal-family-view-followup）。

通知不是 DomainEvent / ActionCard / SpaceMember 的替代真源：每行只保存
「收件人 + 空间 + 领域引用 + 最小安全文案 + 已读时间」。通知已读只改
``read_at``，绝不触碰领域状态或 ActionCard revision。

- ``domain_status`` 与 ActionCard ``revision`` 不落列：读取时从被引用领域
  对象实时投影（单一真源，杜绝快照漂移）；引用行随空间级联删除时通知行
  也级联删除，读取端再校验引用匹配，引用损坏的行不返回。
- ``bridge`` / ``relation`` 两类暂无自然领域来源，允许不产生行（合同只为
  真实存在的行服务，不造假数据）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

NOTIFICATION_KINDS = ("action_card", "space_membership", "bridge", "relation", "steward_suggestion")

_KIND_SQL = f"kind IN ({', '.join(repr(kind) for kind in NOTIFICATION_KINDS)})"


class Notification(Base):
    """One recipient-addressed minimal notification projection row."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_KIND_SQL, name="ck_notifications_kind"),
        # kind='action_card' 的行必须带合法卡片引用（前端合同硬性要求）
        CheckConstraint(
            "kind <> 'action_card' OR action_card_id IS NOT NULL",
            name="ck_notifications_action_card_ref",
        ),
        Index(
            "ix_notifications_recipient_space_read",
            "recipient_account_id",
            "space_id",
            "read_at",
        ),
        Index("ix_notifications_space", "space_id"),
        # kind='steward_suggestion' 的行必须带合法建议引用
        CheckConstraint(
            "kind <> 'steward_suggestion' OR suggestion_id IS NOT NULL",
            name="ck_notifications_suggestion_ref",
        ),
        # 每收件人×空间×建议至多一条通知（防重复卡通知）
        Index(
            "uq_notifications_suggestion",
            "recipient_account_id",
            "space_id",
            "suggestion_id",
            unique=True,
            sqlite_where=text("suggestion_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    recipient_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # 领域引用：kind 对应的引用列（FK 级联：领域行删除 → 通知随之删除）
    action_card_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_cards.id", ondelete="CASCADE"), nullable=True
    )
    space_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("space_members.id", ondelete="CASCADE"), nullable=True
    )
    # steward 建议引用（kind='steward_suggestion'；FK 级联：建议行删除 → 通知随之删除）
    suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_suggestions.id", ondelete="CASCADE"), nullable=True
    )
    # 动作主体（SET NULL：主体档案删除后投影为 null，不阻塞通知历史）
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Notification {self.id} {self.kind} space={self.space_id}"
            f" recipient={self.recipient_account_id} read={self.read_at is not None}>"
        )


__all__ = ["NOTIFICATION_KINDS", "Notification"]
