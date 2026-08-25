"""auth_challenges 表：同名同 PIN 消歧 challenge（architecture.md §2 [AD-2]）。

防重放靠数据库保证：select 时单事务
    UPDATE ... SET used_at WHERE jti=? AND used_at IS NULL AND expires_at>now()
影响行数=0 即拒绝（过期/重放同一处理路径）。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 对外暴露的 challenge_id，随机不可枚举
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    candidate_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 创建时绑定客户端 IP，select 时校验一致
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuthChallenge jti={self.jti!r} used_at={self.used_at}>"
