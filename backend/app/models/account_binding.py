"""account_bindings 表：建档撞名并流绑定请求（09-05 决策 16，纯增量新表）。

建档/邀请查重命中已存在的自注册账号（Account claimed 且 users.created_by IS NULL）
时，不再新建第二份可登录凭据（§0.9：重复建档 = 多出一份凭据），改为创建 pending
绑定请求：被绑定人本人以 PIN + 「这是我」确认后，建档产生的 provisional 人物并回
其既有 user（commands/bindings.py）。

- initiator_id：发起建档的人（撞名请求的发起方）；
- target_id：被绑定的既有自注册 user（唯一能 confirm/reject 的人）；
- person_id：建档产生的 provisional 人物（无 Account）。终态处置会删除该人物行，
  故 FK 为 SET NULL——绑定行作为处置记录保留，人物已并回或废弃；
- status：pending → confirmed | rejected | cancelled；终态不可逆（重复处理 409）。
  confirmed/rejected 由 target 做出，cancelled 由 initiator 做出。

initiator/target 为 CASCADE：主体删除后请求随之消失（person_id SET NULL 的孤儿
provisional 人物仍可由其创建者经既有建档删除路径处置）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ACCOUNT_BINDING_STATUSES = ("pending", "confirmed", "rejected", "cancelled")


class AccountBinding(Base):
    __tablename__ = "account_bindings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','confirmed','rejected','cancelled')",
            name="ck_account_binding_status",
        ),
        Index("ix_account_bindings_target", "target_id"),
        Index("ix_account_bindings_initiator", "initiator_id"),
        Index("ix_account_bindings_person", "person_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    initiator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # provisional 人物并回/废弃后行被删除 → SET NULL，绑定行保留为处置记录
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AccountBinding {self.id} initiator={self.initiator_id} target={self.target_id}"
            f" person={self.person_id} {self.status}>"
        )
