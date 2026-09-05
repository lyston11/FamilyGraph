"""invite_codes 表：家庭/家族/陌生人三类邀请码（09-05 家庭账号开通与注册流程）。

- household/lineage：一次性（max_uses=1）、绑定空间（space_id NOT NULL），
  持码人经既有 SpaceMember pending→active 路径加入该空间；
- stranger：纯拉新归因码（space_id 必须 NULL），多人次、可设使用上限，
  注册者获得自己的独立新家庭空间，与码创建者之间无任何成员关系。
- 码以明文存储（创建者可在设置页查看并分享 …/register?code=XXX）；
  唯一约束 uq_invite_codes_code 兜底，生成与查重由服务层原子完成。

creator_id 为 RESTRICT：邀请码是授信对象，随创建者静默级联会掩盖撤销历史；
创建者删除前须先撤销其码（与 family_spaces.owner_id RESTRICT 同一 §0.5 哲学）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

INVITE_CODE_KINDS = ("household", "lineage", "stranger")


class InviteCode(Base):
    __tablename__ = "invite_codes"
    __table_args__ = (
        CheckConstraint("kind IN ('household','lineage','stranger')", name="ck_invite_code_kind"),
        CheckConstraint(
            "(kind = 'stranger' AND space_id IS NULL) "
            "OR (kind IN ('household','lineage') AND space_id IS NOT NULL)",
            name="ck_invite_code_space_pair",
        ),
        CheckConstraint(
            # SQLite CHECK 语义：NULL 结果视为通过 → household/lineage 必须显式排除 NULL
            "(kind IN ('household','lineage') AND max_uses IS NOT NULL AND max_uses = 1) "
            "OR (kind = 'stranger' AND (max_uses IS NULL OR max_uses >= 1))",
            name="ck_invite_code_max_uses",
        ),
        CheckConstraint(
            "used_count >= 0 AND (max_uses IS NULL OR used_count <= max_uses)",
            name="ck_invite_code_used_count",
        ),
        UniqueConstraint("code", name="uq_invite_codes_code"),
        Index("ix_invite_codes_creator", "creator_id"),
        Index("ix_invite_codes_space", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # household/lineage 必填、stranger 必须 NULL（ck_invite_code_space_pair）
    space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=True
    )
    # stranger 可设上限，NULL=不限次；household/lineage 恒为 1
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<InviteCode {self.id} kind={self.kind} space={self.space_id} "
            f"used={self.used_count}/{self.max_uses}>"
        )
