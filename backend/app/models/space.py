"""家庭空间与成员资格、provisional 引用模型（v2 Foundation，原 m1c AD-3/AD-4）。

v2 语义：
- family_spaces.kind = household | lineage；owner FK 为 RESTRICT：
  删除 owner 前必须移交/显式终止，禁止 FK 级联静默删空间。
- space_members.role = space_admin|member；owner_id 仅兼容镜像，不参与授权。
- space_profile_refs：创建他人选择空间时只建最小节点引用；
  provisional 人物不是 SpaceMember。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SPACE_MEMBER_STATUSES = ("pending", "active", "rejected", "withdrawn", "removed")
SPACE_MEMBER_ROLES = ("space_admin", "member")
# 待处理行的来源（09-20）：决定房主批准与受邀人接受的顺序。
# invite = 房主批准后还需受邀人接受；join_request/code = 房主批准即生效
# （提交方即申请方/兑换方，其提交已是同意）。NULL = 旧行，沿用旧语义。
SPACE_MEMBER_ORIGINS = ("invite", "join_request", "code")
PENDING_EXPIRY_DAYS = 30

# 空间管理者申请（平台运营者审批制，任务 08-30-space-manager-approval）
MANAGER_REQUEST_KINDS = ("space_admin",)
MANAGER_APPLICATION_STATUSES = ("pending", "approved", "rejected")
TRANSFER_CONSENT_STATUSES = ("pending", "accepted", "rejected", "expired")


class FamilySpace(Base):
    __tablename__ = "family_spaces"
    __table_args__ = (
        CheckConstraint("kind IN ('household', 'lineage')", name="ck_family_spaces_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # v2：RESTRICT —— owner 行删除被数据库拒绝，移交/终止流程负责显式处理
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), default="household", nullable=False)
    # 显式家族配对（仅 household 行有值）：所属 lineage 空间。NULL = 未配对，
    # 前端按 owner 唯一匹配回退推断；命令层保证目标 kind='lineage'。
    # RESTRICT 与 owner 同哲学：删除 lineage 前必须先显式解除配对，禁止静默断链。
    lineage_space_id: Mapped[int | None] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FamilySpace {self.id} {self.name!r} kind={self.kind} owner={self.owner_id}>"


class SpaceMember(Base):
    __tablename__ = "space_members"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_space_member_pair"),
        CheckConstraint("role IN ('space_admin','member')", name="ck_sm_role"),
        Index(
            "uq_space_active_admin",
            "space_id",
            unique=True,
            sqlite_where=text("role = 'space_admin' AND status = 'active'"),
        ),
        CheckConstraint(
            "status IN ('pending','active','rejected','withdrawn','removed')",
            name="ck_sm_status",
        ),
        Index("ix_space_members_space", "space_id"),
        Index("ix_space_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SpaceMember space={self.space_id} user={self.user_id}"
            f" role={self.role} {self.status}>"
        )


@event.listens_for(SpaceMember, "before_insert")
@event.listens_for(SpaceMember, "before_update")
def _normalize_legacy_owner_role(_mapper: object, _connection: object, target: SpaceMember) -> None:
    """收敛迁移期 ORM 写入的旧 owner 拼写，不让它落入数据库。

    旧客户端/夹具可能仍构造 ``role='owner'``；该兼容层只做同义词归一化，
    不保留 owner 角色，也不参与授权判断。
    """
    if target.role == "owner":
        target.role = "space_admin"


class SpaceMemberApproval(Base):
    """待处理成员行的房主审批状态（09-20 审批链）。

    独立于 ``space_members``：成员表保持纯成员行语义（历史行不因新列改变形状，
    旧 schema 造数也无需感知审批链）。存在本行 = 该 pending 行由加入链产生：

    - ``origin='invite'``：房主批准后仍需受邀人本人接受；
    - ``origin='join_request' | 'code'``：提交方即申请方/兑换方，房主批准即生效；
    - 无本行 = 历史行，沿用旧语义（受邀人本人接受即可）。

    ``owner_approved_at`` NULL 表示尚未获房主批准。
    """

    __tablename__ = "space_member_approvals"
    __table_args__ = (
        CheckConstraint("origin IN ('invite','join_request','code')", name="ck_sma_origin"),
        Index("ix_space_member_approvals_space", "space_id"),
    )

    member_id: Mapped[int] = mapped_column(
        ForeignKey("space_members.id", ondelete="CASCADE"), primary_key=True
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SpaceMemberApproval member={self.member_id} origin={self.origin} "
            f"approved={self.owner_approved_at is not None}>"
        )


class MemberRelationLabel(Base):
    """成员间自由关系词标注（09-20）。

    加入空间时填写的「我和对方是什么关系」：自由文本（兄弟/朋友/闺蜜均可），
    **不是亲属事实**——绝不进入 source_facts / relations / social_relations，
    不参与关系路径推导、可达性、世代计算或拓扑边；只在家族树与个人页按当前
    授权节点集合渲染一条标注边。

    一对人一条（端点规范化 user_a_id < user_b_id），两端本人均可改，改完即时
    生效，无需对方确认、无需房主审批；清空即删除。
    """

    __tablename__ = "member_relation_labels"
    __table_args__ = (
        UniqueConstraint("space_id", "user_a_id", "user_b_id", name="uq_mrl_pair"),
        CheckConstraint("user_a_id != user_b_id", name="ck_mrl_no_self"),
        CheckConstraint("length(label) BETWEEN 1 AND 64", name="ck_mrl_label_length"),
        Index("ix_member_relation_labels_space", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    user_a_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_b_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<MemberRelationLabel space={self.space_id} "
            f"pair=({self.user_a_id},{self.user_b_id}) label={self.label!r}>"
        )


class SpaceProfileRef(Base):
    """space_profile_refs：provisional 人物在空间中的最小节点引用（v2 F-3）。

    被引用者不是 SpaceMember、不进入推荐资格；可见性仅 lineage_summary 基线。
    """

    __tablename__ = "space_profile_refs"
    __table_args__ = (
        UniqueConstraint("space_id", "user_id", name="uq_space_profile_ref_pair"),
        CheckConstraint("status IN ('active', 'removed')", name="ck_spr_status"),
        Index("ix_space_profile_refs_space", "space_id"),
        Index("ix_space_profile_refs_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SpaceProfileRef space={self.space_id} user={self.user_id} {self.status}>"


class SpaceManagerApplication(Base):
    """空间管理者申请（平台运营者审批制，任务 08-30-space-manager-approval）。

    request_kind 只有 ``space_admin``：申请人成为目标空间的 space_admin。
    申请人须为该空间 active member；owner/space_admin 不适用。

    裁决语义：approve/reject 由 platform_operator 在管理端做出（reject 理由必填）；
    裁决终态不可再变（重复裁决 409）。现有空间 owner 只经 ownership_transfers
    FSM 变更，本模型绝不触碰 family_spaces.owner_id。

    唯一性：同一 (applicant, space, kind) 至多一条 pending。
    """

    __tablename__ = "space_manager_applications"
    __table_args__ = (
        CheckConstraint("request_kind IN ('space_admin')", name="ck_sma_kind"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="ck_sma_status"),
        CheckConstraint("space_id IS NOT NULL", name="ck_sma_space_required"),
        Index(
            "uq_space_manager_application_pending",
            "applicant_user_id",
            "space_id",
            "request_kind",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_space_manager_applications_applicant", "applicant_user_id"),
        Index("ix_space_manager_applications_space", "space_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    applicant_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    request_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # 系统管理员不是家庭 User，裁决人身份必须独立记录（PRD R3：申请记录包含
    # 系统管理员裁决信息）。两列互斥：同一条申请只会由一类主体裁决。
    system_admin_decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("system_admins.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SpaceManagerApplication {self.id} applicant={self.applicant_user_id}"
            f" kind={self.request_kind} {self.status}>"
        )


class ManagerTransferConsent(Base):
    """原管理员对平台审核申请的、可追踪的一次性同意工单。"""

    __tablename__ = "manager_transfer_consents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','rejected','expired')",
            name="ck_mtc_status",
        ),
        UniqueConstraint("application_id", name="uq_mtc_application"),
        Index("ix_manager_transfer_consents_manager", "current_manager_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("space_manager_applications.id", ondelete="CASCADE"), nullable=False
    )
    space_id: Mapped[int] = mapped_column(
        ForeignKey("family_spaces.id", ondelete="CASCADE"), nullable=False
    )
    current_manager_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ManagerTransferConsent application={self.application_id}"
            f" manager={self.current_manager_user_id} {self.status}>"
        )
