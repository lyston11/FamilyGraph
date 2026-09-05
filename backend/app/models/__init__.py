"""ORM 模型汇总：Alembic env.py 挂载 target_metadata 与测试建表使用。"""

from app.models.account import Account
from app.models.account_binding import AccountBinding
from app.models.admin_access import AdminAccessAudit, AdminAccessSession
from app.models.agent import (
    AgentJob,
    AgentMessage,
    AgentRun,
    AgentRunEvent,
    AgentSession,
    AgentToolCall,
)
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.models.audit_log import AuditLog
from app.models.auth_challenge import AuthChallenge
from app.models.base import Base
from app.models.context import ContextBuild, ContextBuildItem
from app.models.controlled_web import (
    WebApprovedURL,
    WebCitation,
    WebPlatformConfig,
    WebRequestUsage,
    WebSpaceConfig,
)
from app.models.derived_fact import DerivedFact
from app.models.invite_code import InviteCode
from app.models.memory import Memory, MemoryCandidate
from app.models.node_position import NodePosition
from app.models.notification import Notification
from app.models.personal_family_view import (
    PersonalFamilyBridge,
    PersonalFamilyView,
    PersonalFamilyViewEdge,
    PersonalFamilyViewNode,
)
from app.models.rag import RAGChunk, RAGDocument
from app.models.refresh_session import RefreshSession
from app.models.relation import Relation
from app.models.relationship_facts import RawRelationInput, SocialRelation, SourceFact
from app.models.space import (
    FamilySpace,
    ManagerTransferConsent,
    SpaceManagerApplication,
    SpaceMember,
    SpaceProfileRef,
)
from app.models.steward import ActionCard, BehaviorProjection, StewardJob
from app.models.system_admin import SystemAdmin, SystemAdminAccount, SystemAdminRefreshSession
from app.models.term_registry import TermEntry, TermUsage
from app.models.user import User
from app.models.v2_foundation import (
    ClaimDispute,
    DataRightRequest,
    DisclosurePreference,
    DomainEvent,
    MemberCreationRequest,
    OwnerInvitation,
    OwnershipTransfer,
    PlatformRoleAssignment,
    ProfileFactReview,
)

__all__ = [
    "Account",
    "AccountBinding",
    "ActionCard",
    "AdminAccessAudit",
    "AdminAccessSession",
    "AgentJob",
    "AgentMessage",
    "AgentProvider",
    "AgentRun",
    "AgentRunEvent",
    "AgentSession",
    "AgentSpaceProviderSetting",
    "AgentToolCall",
    "AuditLog",
    "AuthChallenge",
    "Base",
    "BehaviorProjection",
    "InviteCode",
    "ClaimDispute",
    "ContextBuild",
    "ContextBuildItem",
    "DataRightRequest",
    "DerivedFact",
    "DisclosurePreference",
    "DomainEvent",
    "FamilySpace",
    "MemberCreationRequest",
    "Memory",
    "MemoryCandidate",
    "NodePosition",
    "Notification",
    "OwnerInvitation",
    "OwnershipTransfer",
    "PersonalFamilyBridge",
    "PersonalFamilyView",
    "PersonalFamilyViewEdge",
    "PersonalFamilyViewNode",
    "PlatformRoleAssignment",
    "ProfileFactReview",
    "RAGChunk",
    "RAGDocument",
    "RawRelationInput",
    "RefreshSession",
    "Relation",
    "SocialRelation",
    "SourceFact",
    "ManagerTransferConsent",
    "SpaceManagerApplication",
    "SpaceMember",
    "SpaceProfileRef",
    "StewardJob",
    "SystemAdmin",
    "SystemAdminAccount",
    "SystemAdminRefreshSession",
    "TermEntry",
    "TermUsage",
    "User",
    "WebApprovedURL",
    "WebCitation",
    "WebPlatformConfig",
    "WebRequestUsage",
    "WebSpaceConfig",
]
