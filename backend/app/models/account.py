"""accounts 表：登录凭据与账号生命周期（v2 Foundation）。

与档案 1:0..1（user_id UNIQUE）。v2 起 managed→claimed 状态机权威在本表：
- status：managed（从未登录认领）→ claimed（首登改 PIN 完成），单向，唯一转换点
  在 identity_fsm.claim_account（由 PUT /me/pin 触发）
- claimed_at：转换时间簿记
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("status IN ('managed', 'claimed')", name="ck_accounts_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # 首登强制改 PIN；改毕置 false 并 token_version+1
    pin_must_change: Mapped[bool] = mapped_column(default=True, nullable=False)
    # 敏感操作（改 PIN/重置）+1，get_current_user 比对使旧 access 即刻失效
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 登录限流状态（AD-2）：按 name 连续失败计数，超阈值写 locked_until
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # v2 账号生命周期状态机（见模块 docstring）
    status: Mapped[str] = mapped_column(String(16), default="managed", nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="account")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Account user_id={self.user_id} status={self.status}>"
