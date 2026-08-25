"""audit_log 表：安全审计（architecture.md §2 [AD-2]）。

target_id 不加 FK——审计行是快照语义，被删对象的历史必须保留（AD-5）。
索引与迁移 0002 保持元数据一致：ix_audit_log_created_at / ix_audit_log_actor_id。
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # actor 删除后审计行保留，actor_id 置 NULL（ON DELETE SET NULL）
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_actor_id", "actor_id"),
    )

    @property
    def detail(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.detail_json)
        return value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog id={self.id} action={self.action!r}>"
