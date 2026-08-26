"""ORM 模型汇总：Alembic env.py 挂载 target_metadata 与测试建表使用。"""

from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.auth_challenge import AuthChallenge
from app.models.base import Base
from app.models.node_position import NodePosition
from app.models.refresh_session import RefreshSession
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.models.user import User
from app.models.v2_foundation import (
    ClaimDispute,
    DataRightRequest,
    DisclosurePreference,
    DomainEvent,
    OwnerInvitation,
    OwnershipTransfer,
    PlatformRoleAssignment,
    ProfileFactReview,
)

__all__ = [
    "Account",
    "AuditLog",
    "AuthChallenge",
    "Base",
    "ClaimDispute",
    "DataRightRequest",
    "DisclosurePreference",
    "DomainEvent",
    "NodePosition",
    "OwnershipTransfer",
    "OwnerInvitation",
    "FamilySpace",
    "PlatformRoleAssignment",
    "ProfileFactReview",
    "RefreshSession",
    "Relation",
    "SpaceMember",
    "SpaceProfileRef",
    "User",
]
