"""refresh_sessions 表：refresh 凭据持久化 + 轮换链 + 重用检测（AD-2）。

只存 token_hash（sha256），原始 token 不落库；rotated_from 指向被轮换的旧行。
提交已 revoked 的 token = 重用攻击 → 撤销该用户全部活跃会话。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 轮换链：指向被本次刷新取代的旧 session 行
    rotated_from: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RefreshSession id={self.id} user_id={self.user_id}>"
